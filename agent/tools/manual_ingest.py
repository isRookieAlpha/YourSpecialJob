# -*- coding: utf-8 -*-
"""手动粘贴文本摄入工具（对话通道；微信 MCP 不可用时的兜底）。"""
from datetime import date, datetime

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from loguru import logger

from agent.config import get_settings
from agent.manual_ingest import ingest_manual_text


def _parse_iso_date(s: str) -> date:
    """解析 ISO 日期（容忍错误格式，回退今天）"""
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        logger.warning("target_date 格式错误（应为 YYYY-MM-DD）：{!r}，回退今天", s)
        return date.today()


@tool(parse_docstring=True)
async def ingest_text_messages(
    text: str, group_name: str | None = None, target_date: str | None = None
) -> str:
    """将用户手动粘贴的微信群聊天文本结构化后存入数据库（微信读取失败时的兜底通道）。

    适用于用户在对话中直接粘贴群消息文本的场景；会调用弱模型逐条提取兼职信息。
    群名省略时统一记为"手动复制粘贴识别"（不再使用设置页默认群名），日期省略时用设置页默认日期（空则今天）。

    Args:
        text: 用户粘贴的原始聊天文本（微信复制格式或纯文本均可）。
        group_name: 群名（用于去重与血缘），如 "兼职1群"；用户明确告知来源群时填写，否则省略。
        target_date: 消息日期，YYYY-MM-DD；省略时用设置页默认日期（空则今天）。

    Returns:
        摄入统计的中文摘要（消息数/有效兼职/新入库/重复），供后续查询使用。
    """
    # 记录入库起始时间（供后续 query_jobs(ingested_after=…) 只查本轮新数据）
    ingested_after = datetime.now().astimezone().isoformat(timespec="seconds")

    prefs = get_settings().preferences or {}
    group = (group_name or "").strip() or "手动复制粘贴识别"
    date_str = (target_date or "").strip() or str(prefs.get("default_date") or "").strip()

    # 进度实时推送到前端（custom stream；非流式上下文为 no-op writer）
    try:
        writer = get_stream_writer()

        def progress(message: str) -> None:
            writer({"type": "progress", "message": message})
    except Exception:
        progress = None

    target_d = _parse_iso_date(date_str)
    try:
        if progress:
            progress(f"正在解析粘贴文本并结构化（{group} {target_d.isoformat()}）…")
        stats = await ingest_manual_text(group, target_d, text, progress_cb=progress)
    except Exception as e:
        logger.exception("手动粘贴摄入失败")
        return f"粘贴文本入库失败：{e}\n请检查粘贴内容是否为完整聊天记录，或告知用户稍后重试。"

    return (
        f"粘贴文本入库完成（群={stats.group_name} 日期={stats.target_date}）：\n"
        f"- 解析消息 {stats.total_messages} 条，噪音 {stats.noise_filtered} 条，"
        f"重复 {stats.dup_raw} 条\n"
        f"- 判定有效兼职 {stats.llm_valid} 条\n"
        f"- 新入库 {stats.new_inserted} 条（已存在：批次内重复 {stats.dup_in_run} / "
        f"历史重复 {stats.dup_jsonl} / 数据库重复 {stats.dup_db}）\n"
        f"- 耗时 {stats.elapsed_sec}s。可继续用 query_jobs 查询推荐。\n"
        f"- 本轮新数据过滤标识：ingested_after='{ingested_after}'（推荐时先查这批数据，再查历史）"
        + (f"\n- 高德 MCP 不可用或地址无法定位：{stats.commute_failed} 条兼职通勤暂无法计算"
           if stats.commute_failed else "")
    )