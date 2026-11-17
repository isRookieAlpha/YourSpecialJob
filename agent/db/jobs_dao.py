# -*- coding: utf-8 -*-
"""
jobs 表数据访问层：批量入库去重 + SQL 硬过滤查询（推荐链路核心）。

所有过滤条件走 psycopg 占位符（参数化），禁止 f-string 拼值。
"""
from dataclasses import dataclass
from datetime import date, datetime

from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.models.job import JobExtractItem, compute_content_hash

#1.公共查询列（query_jobs 与 offers_dao 共用，保证行字段一致）
JOB_ROW_COLUMNS = (
    "id", "group_name", "source", "source_ref",
    "work_summary", "work_date", "work_time",
    "commission_min", "commission_max", "commission_unit", "commission_text",
    "location", "physical_intensity", "is_physical_labor",
    "contact", "publisher", "deposit", "settlement_method", "notes", "raw_text",
    "publish_time", "ingested_at", "offer_status",
    "commute_distance_m", "commute_bike_min", "commute_metro_min",
    "commute_transit_min", "commute_drive_min", "commute_updated_at",
)

_NUMERIC_COLUMNS = ("commission_min", "commission_max", "deposit")
_TIME_COLUMNS = ("publish_time", "ingested_at", "commute_updated_at")


def convert_job_row(columns, row) -> dict:
    """统一行转换：date→str、NUMERIC→float、timestamptz→本地 ISO 字符串"""
    result: dict = {}
    for key, value in zip(columns, row):
        if value is None:
            result[key] = None
        elif key == "work_date":
            result[key] = str(value)
        elif key in _NUMERIC_COLUMNS:
            result[key] = float(value)
        elif key in _TIME_COLUMNS:
            result[key] = value.astimezone().isoformat(timespec="seconds")
        else:
            result[key] = value
    return result

#1.入库 SQL（ON CONFLICT 兜底去重，主防线是 UNIQUE(group_name, content_hash)）
_INSERT_SQL = """
INSERT INTO jobs (
    group_name, source, source_ref, raw_text, content_hash, is_valid_job,
    work_summary, is_physical_labor, physical_intensity, work_date, work_time,
    publish_time, commission_text, commission_min, commission_max, commission_unit,
    location, deposit, travel_cost, is_daily_settlement, settlement_method,
    contact, publisher, notes
) VALUES (
    %(group_name)s, %(source)s, %(source_ref)s, %(raw_text)s, %(content_hash)s, TRUE,
    %(work_summary)s, %(is_physical_labor)s, %(physical_intensity)s, %(work_date)s, %(work_time)s,
    %(publish_time)s, %(commission_text)s, %(commission_min)s, %(commission_max)s, %(commission_unit)s,
    %(location)s, %(deposit)s, %(travel_cost)s, %(is_daily_settlement)s, %(settlement_method)s,
    %(contact)s, %(publisher)s, %(notes)s
)
ON CONFLICT ON CONSTRAINT jobs_group_hash_uk DO NOTHING
"""


@dataclass
class JobQueryFilters:
    """query_jobs 的硬过滤条件（None 表示不限）"""

    date_start: date | None = None
    date_end: date | None = None
    commission_min: float | None = None       # 期望佣金下限
    commission_max: float | None = None       # 期望佣金上限
    tolerance: float = 30.0                   # 佣金区间重叠容差（元）
    physical_intensities: list[str] | None = None  # 如 ["轻"]；None=不限
    include_unknown_intensity: bool = True    # 强度未知的记录是否保留
    location_keywords: list[str] | None = None     # 地点关键词（OR 匹配）；None=不限
    group_name: str | None = None                  # 精确群名（如刚入库的截图批次"图片上传_..."）
    source: str | None = None                      # 来源过滤：wechat_auto/manual_text/manual_image/manual_form
    ingested_after: datetime | None = None         # 入库时间下限（本轮新数据）
    ingested_before: datetime | None = None        # 入库时间上限（数据库历史数据）
    limit: int = 20


async def insert_extracted(
    pool: AsyncConnectionPool,
    group_name: str,
    items: list[JobExtractItem],
    source_ref: str,
    fallback_date: date,
    source: str = "wechat_auto",
    with_ids: bool = False,
):
    """批量入库（仅 is_valid_job 且 job 非空的条目）

    Args:
        pool: 连接池
        group_name: 群名
        items: 提取结果列表
        source_ref: 血缘（导出 json 文件名）
        fallback_date: 消息缺省工作日期（通常为查询日期）
        source: 来源标识
        with_ids: True 时额外返回新插入的 job id 列表（供通勤计算使用）

    Returns:
        (new_inserted 新入库条数, dup_db 数据库已存在条数)；
        with_ids=True 时为 (new_inserted, dup_db, inserted_ids)
    """
    #2.整理待插入行（work_date 缺省回填查询日期）
    rows: list[dict] = []
    for item in items:
        if not item.is_valid_job or item.job is None:
            continue
        job = item.job
        row = job.model_dump()
        row.update(
            group_name=group_name,
            source=source,
            source_ref=source_ref,
            raw_text=item.raw_text,
            content_hash=compute_content_hash(group_name, item.raw_text),
            work_date=job.work_date or fallback_date,
        )
        rows.append(row)
    if not rows:
        return (0, 0, []) if with_ids else (0, 0)

    #3.先查本组已存在的 hash，统计 dup_db（同时作为日志可观测性）
    hashes = [r["content_hash"] for r in rows]
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT content_hash FROM jobs WHERE group_name = %s AND content_hash = ANY(%s)",
            (group_name, hashes),
        )
        existing = {r[0] for r in await cur.fetchall()}

    #4.事务内批量插入，ON CONFLICT 双保险；逐条 RETURNING id（通勤计算需要）
    new_rows = [r for r in rows if r["content_hash"] not in existing]
    inserted_ids: list[int] = []
    if new_rows:
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    for row in new_rows:
                        await cur.execute(_INSERT_SQL + " RETURNING id", row)
                        fetched = await cur.fetchone()
                        if fetched:
                            inserted_ids.append(fetched[0])

    dup_db = len(rows) - len(new_rows)
    logger.debug("入库完成 group={} 新插入={} 已存在={}", group_name, len(inserted_ids), dup_db)
    if with_ids:
        return len(inserted_ids), dup_db, inserted_ids
    return len(inserted_ids), dup_db


async def get_jobs_by_ids(pool: AsyncConnectionPool, job_ids: list[int]) -> list[dict]:
    """按 id 批量取兼职行（通勤注解/推送重试用）"""
    if not job_ids:
        return []
    sql = f"SELECT {', '.join(JOB_ROW_COLUMNS)} FROM jobs WHERE id = ANY(%s) ORDER BY id"
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (job_ids,))
        columns = [c.name for c in cur.description]
        return [convert_job_row(columns, row) for row in await cur.fetchall()]


async def query_jobs(pool: AsyncConnectionPool, filters: JobQueryFilters) -> list[dict]:
    """SQL 硬过滤查询兼职（日期区间 / 佣金区间重叠+tolerance / 体力强度 / 地点关键词）

    Args:
        pool: 连接池
        filters: 过滤条件

    Returns:
        兼职记录字典列表（commission 字段已转 float，work_date 转 str）
    """
    #5.动态拼接 WHERE（列名固定，值全部参数化）
    clauses = ["is_valid_job = TRUE", "status = 'active'"]
    params: dict = {}

    if filters.date_start is not None:
        clauses.append("work_date >= %(date_start)s")
        params["date_start"] = filters.date_start
    if filters.date_end is not None:
        clauses.append("work_date <= %(date_end)s")
        params["date_end"] = filters.date_end

    # 佣金区间重叠：招聘区间 [min,max] 与期望区间 [cmin-tol, cmax+tol] 相交即命中
    # 单边开放的招聘区间（只写 min 或 max）按开放处理
    if filters.commission_min is not None or filters.commission_max is not None:
        if filters.commission_max is not None:
            clauses.append("(commission_min IS NULL OR commission_min <= %(cmax)s + %(tol)s)")
            params["cmax"] = filters.commission_max
        if filters.commission_min is not None:
            clauses.append("(commission_max IS NULL OR commission_max >= %(cmin)s - %(tol)s)")
            params["cmin"] = filters.commission_min
        params["tol"] = filters.tolerance

    if filters.physical_intensities:
        clauses.append(
            "(physical_intensity = ANY(%(ints)s) "
            "OR (%(inc_unk)s AND physical_intensity IS NULL))"
        )
        params["ints"] = filters.physical_intensities
        params["inc_unk"] = filters.include_unknown_intensity

    if filters.location_keywords:
        clauses.append("(location IS NOT NULL AND location ILIKE ANY(%(pats)s))")
        params["pats"] = [f"%{kw}%" for kw in filters.location_keywords]

    if filters.group_name:
        clauses.append("group_name = %(group_name)s")
        params["group_name"] = filters.group_name

    if filters.source:
        clauses.append("source = %(source)s")
        params["source"] = filters.source

    if filters.ingested_after is not None:
        clauses.append("ingested_at >= %(ingested_after)s")
        params["ingested_after"] = filters.ingested_after

    if filters.ingested_before is not None:
        clauses.append("ingested_at < %(ingested_before)s")
        params["ingested_before"] = filters.ingested_before

    #6.执行查询（佣金降序给 LLM 软排序一个合理初序）
    columns_sql = ", ".join(JOB_ROW_COLUMNS)
    sql = f"""
        SELECT {columns_sql}
        FROM jobs
        WHERE {" AND ".join(clauses)}
        ORDER BY commission_min DESC NULLS LAST, work_date ASC
        LIMIT %(limit)s
    """
    params["limit"] = filters.limit

    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        columns = [c.name for c in cur.description]
        rows = [convert_job_row(columns, row) for row in await cur.fetchall()]

    logger.debug("query_jobs 命中 {} 条，条件={}", len(rows), filters)
    return rows