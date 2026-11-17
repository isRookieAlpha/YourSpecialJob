# -*- coding: utf-8 -*-
"""PDF 报告导出工具（HITL 拦截：执行前需用户确认）。"""
from datetime import date

from langchain_core.tools import tool
from loguru import logger

from agent.db.jobs_dao import JobQueryFilters, query_jobs as dao_query
from agent.db.pool import get_pool
from agent.report_pdf import render_jobs_pdf


def _parse_iso_date(s: str | None) -> date | None:
    """解析 ISO 日期字符串（容忍空值与格式错误）"""
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        logger.warning("日期参数格式错误（应为 YYYY-MM-DD）：{!r}", s)
        return None


def _filters_summary(
    date_start: date | None,
    date_end: date | None,
    commission_min: float | None,
    commission_max: float | None,
    physical_intensity: str | None,
    location: str | None,
) -> str:
    """查询条件的人类可读摘要（写入 PDF 报告头）"""
    parts = []
    if date_start or date_end:
        parts.append(f"日期 {date_start or '不限'} ~ {date_end or '不限'}")
    if commission_min is not None or commission_max is not None:
        parts.append(f"佣金 {commission_min or 0:g}~{commission_max if commission_max is not None else '不限'}")
    if physical_intensity:
        parts.append(f"体力强度 {physical_intensity}")
    if location:
        parts.append(f"地点关键词 {location}")
    return "；".join(parts) if parts else "无过滤条件"


@tool(parse_docstring=True)
async def export_job_report(
    title: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    commission_min: float | None = None,
    commission_max: float | None = None,
    physical_intensity: str | None = None,
    location: str | None = None,
    limit: int = 20,
) -> str:
    """导出兼职推荐 PDF 报告（生成操作需用户在界面上确认后才会执行）。

    报告内容为查询条件 + 完整兼职记录表格（真实数据库记录），不包含额外推荐语。

    Args:
        title: 报告标题，可空（默认"兼职推荐报告"）。
        date_start: 工作日期下限，YYYY-MM-DD，可空。
        date_end: 工作日期上限，YYYY-MM-DD，可空。
        commission_min: 期望佣金下限（元），可空。
        commission_max: 期望佣金上限（元），可空。
        physical_intensity: 体力强度，取值 "轻"/"中"/"重"，可空。
        location: 地点关键词，多个用逗号分隔，可空。
        limit: 最多导出条数，默认 20。

    Returns:
        生成结果中文摘要（条数与下载链接）；无记录时也会生成并如实说明。
    """
    filters = JobQueryFilters(
        date_start=_parse_iso_date(date_start),
        date_end=_parse_iso_date(date_end),
        commission_min=commission_min,
        commission_max=commission_max,
        physical_intensities=[physical_intensity] if physical_intensity else None,
        include_unknown_intensity=True,
        location_keywords=[k.strip() for k in location.split(",") if k.strip()] if location else None,
        limit=max(1, min(limit, 50)),
    )
    try:
        pool = await get_pool()
        rows = await dao_query(pool, filters)
        path = render_jobs_pdf(
            rows,
            filters_summary=_filters_summary(
                filters.date_start, filters.date_end,
                commission_min, commission_max, physical_intensity, location,
            ),
            title=title or "兼职推荐报告",
        )
    except Exception as e:
        logger.exception("PDF 报告生成失败")
        return f"报告生成失败：{e}。请告知用户稍后重试。"

    return (
        f"报告已生成：{path}\n"
        f"- 包含记录 {len(rows)} 条"
        + ("（无符合条件的记录，已生成空报告）" if not rows else "")
        + "\n请告知用户已导出至上述路径（可在文件管理器中打开），不要编造下载链接。"
    )