# -*- coding: utf-8 -*-
"""
微信 MCP 客户端封装（读取群聊天记录）。

架构约束（Windows 事件循环冲突）：
- 主应用循环用 SelectorEventLoop（psycopg 3.x 不支持 Proactor）
- MCP stdio 子进程必须用 ProactorEventLoop（Windows 上子进程依赖它）
→ 因此 MCP 调用放到独立线程 + 显式 Proactor 循环里执行，互不干扰。

每次读取都是独立临时会话（pywechat 会实际操纵微信窗口，天然不可并发，
并发由 agent.ingest 的模块级锁串行化）。
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from loguru import logger

from agent.config import ROOT_DIR

#1.常量：隔离环境解释器与导出目录（appdata/wechat_history，已 gitignore）
WECHAT_MCP_PYTHON = Path(
    os.getenv("WECHAT_MCP_PYTHON")
    or (Path.home() / ".conda" / "envs" / "SggWechatMCP" / "python.exe")
)
WECHAT_MCP_FOLDER = ROOT_DIR / "data" / "wechat_history"

READ_TIMEOUT_SEC = 600  # pywechat 翻页读 400+ 条消息耗时长，给足 10 分钟
SEND_TIMEOUT_SEC = 90   # 发送是 UI 自动化（打开会话→粘贴→发送），给足 90 秒


class WechatMcpError(RuntimeError):
    """微信 MCP 读取失败（未登录/窗口被占用/超时等），调用方转降级提示"""


def to_mcp_date(d: date) -> str:
    """date → MCP 约定的 YY/M/D 格式（不补零），如 2026-11-03 → "26/11/3" """
    return f"{d.year % 100}/{d.month}/{d.day}"


def parse_mcp_date(s: str) -> date:
    """MCP 日期字符串 → date，如 "26/11/3" → 2026-11-03（年份补 20xx）"""
    parts = [int(p) for p in s.split("/")]
    if len(parts) != 3:
        raise ValueError(f"日期格式应为 YY/M/D：{s!r}")
    return date(2000 + parts[0], parts[1], parts[2])


def find_source_ref(group_name: str) -> str:
    """在导出目录中找该群最新的聊天记录文件名（血缘用；找不到返回兜底名）"""
    candidates = sorted(
        WECHAT_MCP_FOLDER.glob(f"与{group_name}的*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0].name
    return f"与{group_name}的<未找到导出文件>.json"


BLOCK_SEP = "-" * 30


def parse_history_text(text: str) -> list[dict]:
    """解析 MCP 返回的聊天记录纯文本为消息字典列表

    实测格式（qwen 无关，mcp_server_wechat 0.8）：
        获取到 N 条与 {群} 在 {日期} 的聊天记录

        发送者: False
        时间: 10:09
        消息: 动画表情
        ------------------------------
        （重复）
    消息体可能跨多行，直到分隔线为止。

    Args:
        text: 工具返回的正文文本

    Returns:
        消息字典列表（index/发送者/时间/消息）
    """
    messages: list[dict] = []
    #1.去掉首行统计说明
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text
    for block in body.split(BLOCK_SEP):
        block = block.strip()
        if not block:
            continue
        sender, msg_time, msg_lines = "", "", []
        for line in block.split("\n"):
            #1.消息体开始后所有行都是续行：正文里可能含"时间: 11🈷️11.12"这类行，
            #  不能再当元数据解析（曾把 21:26 覆盖成非时间文本，导致发布时间保模型误值）
            if msg_lines:
                msg_lines.append(line)
                continue
            if line.startswith("发送者:"):
                sender = line[len("发送者:"):].strip()
            elif line.startswith("时间:"):
                msg_time = line[len("时间:"):].strip()
            elif line.startswith("消息:"):
                msg_lines.append(line[len("消息:"):])
        messages.append({
            "index": len(messages),
            "发送者": sender,
            "时间": msg_time,
            "消息": "\n".join(msg_lines).strip(),
        })
    return messages


def _extract_text(result) -> str:
    """从 MCP 工具返回中取出正文文本（content blocks / str 两种形态）"""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        texts = [b.get("text", "") for b in result if isinstance(b, dict)]
        return "\n".join(t for t in texts if t)
    raise WechatMcpError(f"微信返回格式异常：{type(result).__name__}")


def _make_client():
    """创建微信 MCP stdio 客户端（每次调用独立临时会话）"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    return MultiServerMCPClient({
        "wechat": {
            "transport": "stdio",
            "command": str(WECHAT_MCP_PYTHON),
            "args": ["-m", "mcp_server_wechat", f"--folder-path={WECHAT_MCP_FOLDER}"],
        }
    })


async def _invoke_tool_async(tool_name: str, arguments: dict, timeout: int) -> str:
    """在 Proactor 循环内调用任意微信 MCP 工具，返回正文文本"""
    client = _make_client()
    tools = await client.get_tools()
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        raise WechatMcpError(f"微信 MCP 未提供 {tool_name} 工具")

    result = await asyncio.wait_for(tool.ainvoke(arguments), timeout=timeout)
    return _extract_text(result)


async def _read_async(
    group_name: str, target_date: str, allow_empty: bool = False,
    timeout: int = READ_TIMEOUT_SEC, search_pages: int = 1,
) -> list[dict]:
    """在 Proactor 循环内执行的实际 MCP 读取（get_tools + 工具调用）"""
    logger.info("开始读取微信聊天记录：群={} 日期={}（请勿操作微信）", group_name, target_date)
    text = await _invoke_tool_async(
        "wechat_get_chat_history",
        {"to_user": group_name, "target_date": target_date, "search_pages": search_pages},
        timeout,
    )

    #2.实测返回 content blocks 列表，正文是纯文本（发送者/时间/消息 块）
    messages = parse_history_text(text)
    if not messages and not allow_empty:
        raise WechatMcpError(f"微信返回内容为空或格式无法识别：{text[:200]!r}")
    logger.info("MCP 返回解析完成：{} 条消息", len(messages))
    return messages


async def _send_async(to_user: str, message: str) -> dict:
    """在 Proactor 循环内调用 wechat_send_message（调用方需持有窗口锁）"""
    logger.info("发送微信消息：to={} 长度={}", to_user, len(message))
    text = await _invoke_tool_async(
        "wechat_send_message",
        {"to_user": to_user, "message": message},
        SEND_TIMEOUT_SEC,
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise WechatMcpError(f"发送结果格式异常：{text[:200]!r}") from e
    if not isinstance(data, dict) or data.get("status") != "success":
        detail = data.get("message") if isinstance(data, dict) else text[:200]
        raise WechatMcpError(f"微信发送失败：{detail}")
    return data


def _run_in_proactor_loop(coro) -> list[dict]:
    """在独立线程的 Proactor 循环中跑协程（MCP stdio 子进程需要）"""
    if sys.platform == "win32" and hasattr(asyncio, "ProactorEventLoop"):
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def call_wechat_get_history(
    group_name: str, target_date: str, allow_empty: bool = False,
    timeout: int = READ_TIMEOUT_SEC, search_pages: int = 1,
) -> list[dict]:
    """读取指定会话在指定日期的聊天记录（异步入口，内部切到 Proactor 线程）

    Args:
        group_name: 微信群名或好友备注/昵称
        target_date: YY/M/D 格式日期（如 "26/11/3"）
        allow_empty: 当天无消息时返回空列表而不是抛错（后台跟踪用）
        timeout: 超时秒数（默认 600s；后台跟踪/探针可传更短）
        search_pages: 搜索会话时翻页次数（好友不在最近会话时需调大）

    Returns:
        消息字典列表（index/发送者/时间/消息）

    Raises:
        WechatMcpError: 读取失败/超时/格式异常
    """
    try:
        return await asyncio.to_thread(
            _run_in_proactor_loop,
            _read_async(group_name, target_date, allow_empty, timeout, search_pages),
        )
    except WechatMcpError:
        raise
    except asyncio.TimeoutError as e:
        raise WechatMcpError(f"读取超时（>{READ_TIMEOUT_SEC}s），微信可能卡住或未登录") from e
    except Exception as e:
        raise WechatMcpError(f"{type(e).__name__}: {e}") from e


async def send_wechat_message(to_user: str, message: str) -> dict:
    """发送微信消息（UI 自动化；调用方必须持有 agent.wechat_window.wechat_window_lock）

    Args:
        to_user: 目标会话（文件传输助手 / 好友 / 群名）
        message: 消息正文（≤1800 字，避免触发文件发送路径）

    Returns:
        MCP 成功响应字典（含 status="success"）

    Raises:
        WechatMcpError: 未登录/工具失败/超时/结果异常
    """
    try:
        return await asyncio.to_thread(_run_in_proactor_loop, _send_async(to_user, message))
    except WechatMcpError:
        raise
    except asyncio.TimeoutError as e:
        raise WechatMcpError(f"发送超时（>{SEND_TIMEOUT_SEC}s），微信可能卡住或未登录") from e
    except Exception as e:
        raise WechatMcpError(f"{type(e).__name__}: {e}") from e


def combine_publish_time(msg_time: str | None, fallback_date: date) -> datetime | None:
    """把消息时刻（如 "10:15"）与查询日期合成发布时间（带本地时区）

    消息时刻来自微信 UI，是本地时间；必须显式赋予本地时区，
    避免 naive datetime 被误当 UTC（曾导致推送时间 +8 小时）。

    Args:
        msg_time: 消息时刻原文（可能为空或格式异常）
        fallback_date: 查询日期

    Returns:
        合成后的 aware datetime；无法解析时返回 None
    """
    if not msg_time:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.strptime(msg_time.strip(), fmt)
            return datetime.combine(fallback_date, t.time()).astimezone()
        except ValueError:
            continue
    return None