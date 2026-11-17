# -*- coding: utf-8 -*-
"""兼职查询工具：SQL 硬过滤（日期/佣金/体力强度/地点/群名/来源/入库时间），返回文本供 LLM 软排序。

同时通过 custom 流推送结构化候选（{"type":"offers","items":[...]}），
供前端右侧"心动的offer"面板使用（排除 raw_text，控制 SSE 帧大小）。
"""
from datetime import date, datetime

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from loguru import logger

from agent.db.jobs_dao import (
    JobQueryFilters,
    get_jobs_by_ids,
    query_jobs as dao_query,
)
from agent.db.offers_dao import set_offer_status
from agent.db.pool import get_pool
from agent.formatting import format_commute, format_source

#1.面板需要的字段（不含 raw_text/deposit 等长字段）
_OFFER_ROW_KEYS = (
    "id", "work_summary", "work_date", "work_time",
    "commission_min", "commission_max", "commission_unit", "commission_text",
    "location", "physical_intensity", "contact", "publisher", "notes",
    "source", "group_name", "publish_time", "ingested_at", "offer_status",
    "commute_distance_m", "commute_bike_min", "commute_transit_min", "commute_drive_min",
)

_STATUS_LABEL = {"favorite": "已是心动的offer", "todo": "已是ToDo待办"}


def _public_offer_row(row: dict) -> dict:
    """构造 SSE offers 事件的单行数据"""
    item = {k: row.get(k) for k in _OFFER_ROW_KEYS}
    item["source_label"] = format_source(row)
    return item


def _parse_iso_date(s: str | None) -> date | None:
    """解析 ISO 日期字符串（容忍空值与格式错误）"""
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        logger.warning("日期参数格式错误（应为 YYYY-MM-DD）：{!r}", s)
        return None


def _parse_iso_dt(s: str | None) -> datetime | None:
    """解析 ISO 时间字符串（入库时间过滤；容忍空值与格式错误）"""
    if not s:
        return None
    raw = s.strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("时间参数格式错误（应为 ISO 时间）：{!r}", s)
        return None


def _format_rows(rows: list[dict]) -> str:
    """把查询结果格式化为 LLM 可读文本"""
    if not rows:
        return "未找到符合条件的兼职。可建议用户放宽日期/佣金条件，或先读取更多日期的群消息。"
    lines = [f"共命中 {len(rows)} 条："]
    for r in rows:
        commission = "-"
        if r.get("commission_min") is not None or r.get("commission_max") is not None:
            lo = r.get("commission_min")
            hi = r.get("commission_max")
            unit = r.get("commission_unit") or ""
            if lo is not None and hi is not None and lo != hi:
                commission = f"{lo:g}~{hi:g}{unit}"
            else:
                commission = f"{(lo if lo is not None else hi):g}{unit}"
        elif r.get("commission_text"):
            commission = r["commission_text"]
        status = _STATUS_LABEL.get(r.get("offer_status"))
        status_text = f" | 状态={status}" if status else ""
        lines.append(
            f"- id={r['id']} | {r.get('work_summary') or '未命名'} | "
            f"日期={r.get('work_date') or '未知'} 时段={r.get('work_time') or '未知'} | "
            f"佣金={commission} | 地点={r.get('location') or '未知'} | "
            f"体力={r.get('physical_intensity') or '未知'} | "
            f"联系={r.get('contact') or '未知'} | "
            f"发布者={r.get('publisher') or '未知'} | "
            f"通勤={format_commute(r)} | "
            f"来源={format_source(r)}{status_text}"
        )
    return "\n".join(lines)


@tool(parse_docstring=True)
async def query_jobs(
    date_start: str | None = None,
    date_end: str | None = None,
    commission_min: float | None = None,
    commission_max: float | None = None,
    physical_intensity: str | None = None,
    location: str | None = None,
    group_name: str | None = None,
    source: str | None = None,
    ingested_after: str | None = None,
    ingested_before: str | None = None,
    limit: int = 20,
) -> str:
    """查询已入库的兼职，按日期区间/佣金区间/体力强度/地点/群名/来源/入库时间做 SQL 硬过滤。

    Args:
        date_start: 工作日期下限，YYYY-MM-DD，可空。
        date_end: 工作日期上限，YYYY-MM-DD，可空。
        commission_min: 期望佣金下限（元），可空。
        commission_max: 期望佣金上限（元），可空。
        physical_intensity: 体力强度，取值 "轻"/"中"/"重"，可空。
        location: 地点关键词，多个用逗号分隔（如 "静安,徐汇"），可空。
        group_name: 只看指定群或截图批次（如 "兼职2群"、"图片上传_20261105_153012"），可空。
        source: 来源过滤，取值 wechat_auto/manual_text/manual_image/manual_form，可空。
        ingested_after: 入库时间下限（ISO 时间），用于只看本轮新入库的数据，可空。
        ingested_before: 入库时间上限（ISO 时间），用于查数据库历史数据，可空。
        limit: 最多返回条数，默认 20。

    Returns:
        候选兼职列表文本（id/摘要/日期时段/佣金/地点/体力/联系/来源）；无结果时说明原因。
    """
    filters = JobQueryFilters(
        date_start=_parse_iso_date(date_start),
        date_end=_parse_iso_date(date_end),
        commission_min=commission_min,
        commission_max=commission_max,
        physical_intensities=[physical_intensity] if physical_intensity else None,
        include_unknown_intensity=True,
        location_keywords=[k.strip() for k in location.split(",") if k.strip()] if location else None,
        group_name=(group_name or "").strip() or None,
        source=(source or "").strip() or None,
        ingested_after=_parse_iso_dt(ingested_after),
        ingested_before=_parse_iso_dt(ingested_before),
        limit=max(1, min(limit, 50)),
    )
    pool = await get_pool()
    rows = await dao_query(pool, filters)

    #2.推送结构化候选给前端"心动的offer"侧栏（非流式上下文/序列化失败不影响查询）
    try:
        writer = get_stream_writer()
        writer({"type": "offers", "items": [_public_offer_row(r) for r in rows]})
    except Exception:
        pass

    return _format_rows(rows)


@tool(parse_docstring=True)
async def select_offers(job_ids: list[int]) -> str:
    """声明本轮最终推荐的兼职（网页端「心动的offer」面板只展示这些条目）。

    完成推荐文本后调用一次，传入实际推荐给用户的兼职 id（按推荐顺序，来自 query_jobs 的 id）。
    两段式推荐（新数据 + 历史数据）请合并为一次调用。

    Args:
        job_ids: 推荐的兼职 id 列表（按推荐顺序，最多 20 个）。

    Returns:
        确认文本：带编号的推荐清单（微信端可回复“添加第 N 条”；网页端面板同步更新）。
    """
    ids = [int(i) for i in (job_ids or [])][:20]
    if not ids:
        return "未提供兼职 id，未展示推荐面板。"
    pool = await get_pool()
    rows = await get_jobs_by_ids(pool, ids)
    by_id = {row["id"]: row for row in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    #1.只展示可添加的条目（已收藏/ToDo 的由左侧推荐文本说明状态）
    items = [_public_offer_row(row) for row in ordered if row.get("offer_status") == "none"]
    try:
        writer = get_stream_writer()
        #2.replace=True：覆盖 query_jobs 推送的候选池，保证右侧与左侧推荐一致
        writer({"type": "offers", "items": items, "replace": True})
    except Exception:
        pass
    if not items:
        return "未找到可展示的推荐（id 无效或已加入心动的offer）。请在推荐文本中说明情况即可。"

    #3.统一返回编号清单：微信端没有侧栏，用户可回复"添加第 N 条"；网页端面板同步更新
    lines = [f"已记录本轮推荐（{len(items)} 条）："]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {item.get('work_summary') or '未命名'}（id={item.get('id')}）")
    lines.append("网页端可在「心动的offer」面板查看；微信端可回复「添加第 N 条」加入心动的offer。")
    return "\n".join(lines)


@tool(parse_docstring=True)
async def add_to_offers(job_ids: list[int]) -> str:
    """把兼职加入「心动的offer」（收藏）。微信端和网页端都可用。

    用户要求"把这条/第 N 条加入心动/收藏"时调用；id 来自 query_jobs 结果或你刚才的推荐列表。
    已在收藏或 ToDo 中的条目不会重复添加或降级。

    Args:
        job_ids: 要加入心动的offer的兼职 id 列表（最多 20 个）。

    Returns:
        中文结果（成功加入的名称与条数、已在收藏/ToDo 的条数）。
    """
    ids = [int(i) for i in (job_ids or [])][:20]
    if not ids:
        return "未提供兼职 id，未执行添加。"
    pool = await get_pool()
    updated, skipped = await set_offer_status(pool, ids, "favorite", mode="promote")
    parts: list[str] = []
    if updated:
        names = "、".join(str(row.get("work_summary") or "未命名") for row in updated[:5])
        parts.append(f"已加入心动的offer：{names}（共 {len(updated)} 条）")
    if skipped:
        parts.append(f"{len(skipped)} 条已在收藏或 ToDo 中，未重复添加")
    return "；".join(parts) or "没有可添加的兼职（id 无效）。"