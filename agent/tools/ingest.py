# -*- coding: utf-8 -*-
"""微信群消息摄入工具（对话通道；前端按钮通道走 backend/routers/ingest.py）。

群名/日期参数可省略：省略时使用设置页配置的默认群名与默认日期
（默认日期为空则今天），避免 agent 反复反问用户。
"""
from datetime import date, datetime, timedelta

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from loguru import logger

from agent.config import get_settings
from agent.ingest import ingest_group
from agent.mcp_wechat import parse_mcp_date, to_mcp_date
from agent.preferences import (
    MAX_DEFAULT_READ_DAYS,
    resolve_default_date_range,
    resolve_wechat_groups,
)


def _parse_date(raw: str) -> date:
    """解析 YYYY-MM-DD 或 YY/M/D（失败抛 ValueError）"""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return parse_mcp_date(raw)


@tool(parse_docstring=True)
async def ingest_wechat_group_messages(
    group_name: str | None = None,
    target_date: str | None = None,
    target_date_end: str | None = None,
) -> str:
    """读取指定微信群在指定日期（或日期范围）的聊天记录，结构化后存入数据库。

    读取会实际操作微信窗口（打开聊天历史翻页），耗时几十秒到几分钟；
    日期范围按天逐日读取，一次最多 30 天（30 天约需 7 分钟）。
    用户没有指定群名或日期时可省略参数：将使用设置页配置的默认群名与默认日期范围
    （默认范围为空则为今天），不要向用户反问群名/日期，除非工具返回提示未配置群名。

    Args:
        group_name: 微信群名，如 "兼职1群"；省略时使用设置页默认读取群。
        target_date: 起始日期，YY/M/D（如 "26/11/3"）或 YYYY-MM-DD；省略时使用设置页默认范围。
        target_date_end: 结束日期（含当天）；省略时只读 target_date 当天。

    Returns:
        摄入统计的中文摘要（天数/总条数/噪音/有效兼职/新入库/重复），供后续查询使用。
    """
    #1.记录入库起始时间（供后续 query_jobs(ingested_after=…) 只查本轮新数据）
    ingested_after = datetime.now().astimezone().isoformat(timespec="seconds")

    #2.微信访问总开关（设置页关闭时不允许读取微信）
    prefs = get_settings().preferences or {}
    if not bool(prefs.get("wechat_access_enabled", True)):
        return (
            "微信访问已在设置页关闭，无法读取微信群消息。"
            "请让用户到设置页勾选「允许 YourSpecialJob 智能体访问您的微信」，"
            "或改用图片上传/手动复制粘贴方式提供兼职信息。"
        )

    #3.群列表：显式 group_name 优先；否则读取设置页「默认读取群名」的全部群
    explicit_group = (group_name or "").strip()
    groups = [explicit_group] if explicit_group else resolve_wechat_groups(prefs)
    if not groups:
        return (
            "未指定群名，且设置页未配置默认读取群。"
            "请让用户提供微信群名（如 兼职1群），或先到设置页「微信设置」配置默认读取群名。"
        )
    #4.日期解析：显式参数优先；否则用设置页默认范围（最多 30 天）
    try:
        if (target_date or "").strip():
            start = _parse_date(target_date.strip())
            end = _parse_date(target_date_end.strip()) if (target_date_end or "").strip() else start
        else:
            start, end = resolve_default_date_range(prefs)
    except ValueError as e:
        return f"{e}（应为 YY/M/D 或 YYYY-MM-DD）"
    if end < start:
        return "结束日期不能早于起始日期。"
    day_count = (end - start).days + 1
    if day_count > MAX_DEFAULT_READ_DAYS:
        return f"一次最多读取 {MAX_DEFAULT_READ_DAYS} 天，请缩小日期范围后重试。"
    date_labels = [to_mcp_date(start + timedelta(days=i)) for i in range(day_count)]

    # 进度实时推送到前端（custom stream；非流式上下文为 no-op writer）
    try:
        writer = get_stream_writer()

        def progress(message: str) -> None:
            writer({"type": "progress", "message": message})
    except Exception:
        progress = None

    #5.逐群逐天读取（每天独立走 ingest_group，保证 work_date/publish_time 正确）
    totals = {
        "total_messages": 0, "noise_filtered": 0, "dup_raw": 0, "batches": 0,
        "llm_valid": 0, "new_inserted": 0, "dup_in_run": 0, "dup_jsonl": 0,
        "dup_db": 0, "commute_failed": 0, "elapsed_sec": 0.0,
    }
    completed_units = 0
    per_group: dict[str, dict] = {}
    errors: list[str] = []

    for group in groups:
        group_stat = {"completed_days": 0, "new_inserted": 0, "error": ""}
        for index, date_label in enumerate(date_labels, 1):
            if progress:
                prefix = f"{group} " if len(groups) > 1 else ""
                progress(f"读取{prefix}第 {index}/{day_count} 天：{date_label}（请勿操作微信）")
            try:
                stats = await ingest_group(group, date_label, progress_cb=progress)
            except Exception as e:
                logger.exception("ingest 工具执行失败 group={} date={}", group, date_label)
                group_stat["error"] = f"{date_label}: {e}"
                errors.append(f"{group} {date_label}: {e}")
                break  # 该群停止，继续下一个群
            completed_units += 1
            group_stat["completed_days"] += 1
            group_stat["new_inserted"] += getattr(stats, "new_inserted", 0)
            for key in totals:
                totals[key] += getattr(stats, key, 0)
        per_group[group] = group_stat

    if completed_units == 0:
        return (
            f"微信读取失败：{'；'.join(errors[:3]) or '未读取到数据'}\n"
            "请告知用户：确保 PC 微信已登录且执行期间不要操作微信；"
            "若仍失败，可让用户手动粘贴群消息作为兜底。"
        )

    range_text = (
        f"{start.isoformat()}（单日）" if day_count == 1
        else f"{start.isoformat()} ~ {end.isoformat()}，共 {day_count} 天"
    )
    group_line = "、".join(
        f"{name}（{stat['completed_days']}/{day_count} 天）"
        for name, stat in per_group.items()
    )
    result = (
        f"读取完成（{len(groups)} 个群，日期={range_text}）：\n"
        f"- 群读取情况：{group_line}\n"
        f"- 原始消息 {totals['total_messages']} 条，"
        f"噪音 {totals['noise_filtered']} 条，刷屏重复 {totals['dup_raw']} 条\n"
        f"- 送模型 {totals['batches']} 批，判定有效兼职 {totals['llm_valid']} 条\n"
        f"- 新入库 {totals['new_inserted']} 条（已存在：批次内重复 {totals['dup_in_run']} / "
        f"历史重复 {totals['dup_jsonl']} / 数据库重复 {totals['dup_db']}）\n"
        f"- 耗时 {totals['elapsed_sec']:.1f}s。可继续用 query_jobs 查询推荐。\n"
        f"- 本轮新数据过滤标识：ingested_after='{ingested_after}'（推荐时先查这批数据，再查历史）"
    )
    if totals["commute_failed"]:
        result += f"\n- 高德 MCP 不可用或地址无法定位：{totals['commute_failed']} 条兼职通勤暂无法计算"
    if errors:
        result += "\n- 读取失败：" + "；".join(errors[:5])
    return result