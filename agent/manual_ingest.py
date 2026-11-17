# -*- coding: utf-8 -*-
"""
手动粘贴文本摄入：解析用户粘贴的聊天文本 → 公共管道结构化入库。

解析支持两种常见粘贴格式（粘贴自微信 PC 端或手机端）：
1. 带行头格式：每段消息首行为 "发送者 2026-11-03 10:15"（日期可省），
   其后的行直到下一个行头（或空行分隔的新行头）属于该条消息体（多行合并）。
2. 纯文本格式：按空行分段，每段一条消息（无发送者/时间）。

输出与 agent.mcp_wechat.parse_history_text 同构：{index, 发送者, 时间, 消息}。
"""
import re
from datetime import date

from loguru import logger

from agent.ingest import ingest_messages
from agent.models.job import IngestStats
from agent.models_factory import is_model_configured

#1.行头正则：发送者（≤30 字符，不含换行）+ 可选日期 + 时间
#   "张三 2026-11-03 10:15" / "张三 10:15" / "张三 2026/11/3 10:15"
_HEADER_RE = re.compile(
    r"^(?P<sender>.{1,30}?)\s+"
    r"(?:(?P<date>\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+)?"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)$"
)

#2.纯文本分段：连续非空行为一段（段内换行保留为消息体）
_BLOCK_SPLIT_RE = re.compile(r"\n\s*\n")


def parse_pasted_text(text: str) -> list[dict]:
    """解析粘贴的聊天文本为消息字典列表

    Args:
        text: 用户粘贴的原始文本

    Returns:
        消息字典列表（index/发送者/时间/消息）；空文本抛 ValueError

    Raises:
        ValueError: 文本为空或无可解析消息
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("粘贴内容为空")

    lines = raw.split("\n")

    #3.先探测是否带行头（至少命中一行才走行头模式）
    header_hits = 0
    for line in lines:
        if line.strip() and _HEADER_RE.match(line.strip()):
            header_hits += 1
    use_header = header_hits >= 1

    messages: list[dict] = []
    if use_header:
        sender, msg_time, body_lines = "", "", []

        def flush() -> None:
            body = "\n".join(body_lines).strip()
            if body:
                messages.append({
                    "index": len(messages),
                    "发送者": sender,
                    "时间": msg_time,
                    "消息": body,
                })

        for line in lines:
            stripped = line.strip()
            m = _HEADER_RE.match(stripped) if stripped else None
            if m:
                flush()
                sender = m.group("sender").strip()
                msg_time = m.group("time")  # 日期部分忽略（消息体里的日期优先，模型按提示词换算）
                body_lines = []
            else:
                body_lines.append(line)
        flush()
    else:
        #4.纯文本模式：按空行分段，每段一条消息
        blocks = [b.strip() for b in _BLOCK_SPLIT_RE.split(raw) if b.strip()]
        for block in blocks:
            messages.append({
                "index": len(messages),
                "发送者": "",
                "时间": "",
                "消息": block,
            })

    if not messages:
        raise ValueError("未能从粘贴内容中解析出消息")
    logger.info("粘贴文本解析完成：{} 条消息（{}模式）", len(messages),
                "行头" if use_header else "纯文本")
    return messages


async def ingest_manual_text(
    group_name: str,
    target_date: date,
    text: str,
    progress_cb=None,
) -> IngestStats:
    """手动粘贴文本 → 公共管道入库（source=manual_text）

    Args:
        group_name: 群名（去重与血缘用；用户可自拟，如 "手动粘贴"）
        target_date: 消息日期（缺省工作日期）
        text: 粘贴的原始文本
        progress_cb: 进度回调（可选）

    Returns:
        摄入统计 IngestStats

    Raises:
        RuntimeError: 弱模型未配置 / 粘贴内容无法解析
    """
    if not is_model_configured("weak"):
        raise RuntimeError("弱模型未配置，请先在设置页或 .env 中填写弱模型 API key")

    try:
        messages = parse_pasted_text(text)
    except ValueError as e:
        raise RuntimeError(f"粘贴内容解析失败：{e}") from e

    return await ingest_messages(
        group_name=group_name,
        target_date_label=target_date.isoformat(),
        target_d=target_date,
        messages=messages,
        source="manual_text",
        source_ref="manual_paste",
        progress_cb=progress_cb,
    )