# -*- coding: utf-8 -*-
"""
心动的offer 数据访问层：状态流转 / 手动录入 / 编辑 / 删除 / 列表查询。

行契约与 jobs_dao.convert_job_row 一致，并附加派生字段：
source_label / work_start / work_end / work_hours / work_time_defaulted /
estimated_income / income_unknown（供 API、SSE、统计页、PDF 共用）。
"""
import hashlib
from uuid import uuid4

from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.db.jobs_dao import JOB_ROW_COLUMNS, convert_job_row
from agent.formatting import format_source
from agent.income import estimate_income
from agent.models.offer import ManualOfferInput, OfferUpdateInput
from agent.work_time import DEFAULT_END, DEFAULT_HOURS, DEFAULT_START, parse_work_time

_COLUMNS_SQL = ", ".join(JOB_ROW_COLUMNS)

#1.编辑白名单（OfferUpdateInput 字段 → jobs 列名一致，直接映射）
_UPDATE_FIELDS = (
    "work_summary", "work_date", "work_time",
    "commission_min", "commission_max", "commission_unit",
    "location", "contact", "notes", "physical_intensity",
)


def enrich_row(row: dict) -> dict:
    """附加派生字段（来源文案 / 时段解析 / 收入估算）"""
    row["source_label"] = format_source(row)
    work = parse_work_time(row.get("work_time"))
    row["work_start"] = work.start if work else DEFAULT_START
    row["work_end"] = work.end if work else DEFAULT_END
    row["work_hours"] = work.hours if work else DEFAULT_HOURS
    row["work_time_defaulted"] = work is None
    income, unknown = estimate_income(
        row.get("commission_min"), row.get("commission_max"),
        row.get("commission_unit"), row.get("work_time"),
    )
    row["estimated_income"] = income
    row["income_unknown"] = unknown
    return row


async def list_offers(
    pool: AsyncConnectionPool,
    statuses: tuple[str, ...] = ("favorite", "todo"),
    limit: int = 500,
) -> list[dict]:
    """列出心动的offer / ToDo（按 id 插入顺序稳定排序）

    排序键只用不可变的 id（而不是 offer_updated_at/work_date）：
    编辑或升降级不会改变记录位置；删除前面的记录后，后面的记录自然前移一位。
    """
    sql = f"""
        SELECT {_COLUMNS_SQL} FROM jobs
        WHERE offer_status = ANY(%s)
        ORDER BY id ASC
        LIMIT %s
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (list(statuses), limit))
        columns = [c.name for c in cur.description]
        rows = [enrich_row(convert_job_row(columns, r)) for r in await cur.fetchall()]
    return rows


async def get_offers_by_ids(pool: AsyncConnectionPool, job_ids: list[int]) -> list[dict]:
    """按 id 取行（保持输入顺序无关，按 id 排序）"""
    if not job_ids:
        return []
    sql = f"SELECT {_COLUMNS_SQL} FROM jobs WHERE id = ANY(%s) ORDER BY id"
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (job_ids,))
        columns = [c.name for c in cur.description]
        return [enrich_row(convert_job_row(columns, r)) for r in await cur.fetchall()]


async def set_offer_status(
    pool: AsyncConnectionPool,
    job_ids: list[int],
    status: str,
    mode: str = "set",
) -> tuple[list[dict], list[dict]]:
    """更新 offer 状态

    Args:
        status: favorite / todo / none
        mode: set 无条件写；promote 仅允许升级（todo 不降级为 favorite）

    Returns:
        (updated_rows, skipped_rows[{id, offer_status}])
    """
    #2.promote 模式加前置状态条件（防止误降级）
    if mode == "promote" and status == "favorite":
        where = "id = ANY(%s) AND offer_status = 'none'"
    elif mode == "promote" and status == "todo":
        where = "id = ANY(%s) AND offer_status IN ('none', 'favorite')"
    else:
        where = "id = ANY(%s)"

    sql = f"""
        UPDATE jobs SET offer_status = %s, offer_updated_at = now()
        WHERE {where}
        RETURNING {_COLUMNS_SQL}
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (status, job_ids))
        columns = [c.name for c in cur.description]
        updated = [enrich_row(convert_job_row(columns, r)) for r in await cur.fetchall()]

        updated_ids = {r["id"] for r in updated}
        missing = [i for i in job_ids if i not in updated_ids]
        skipped: list[dict] = []
        if missing:
            cur = await conn.execute(
                "SELECT id, offer_status FROM jobs WHERE id = ANY(%s)", (missing,)
            )
            skipped = [{"id": r[0], "offer_status": r[1]} for r in await cur.fetchall()]
    logger.info("offer 状态更新：{} 条成功，{} 条跳过（status={} mode={}）",
                len(updated), len(skipped), status, mode)
    return updated, skipped


def _build_manual_raw_text(data: ManualOfferInput) -> str:
    """手动录入的 raw_text（字段拼接，保证 NOT NULL 与可读血缘）"""
    commission = ""
    if data.commission_min is not None or data.commission_max is not None:
        lo = data.commission_min if data.commission_min is not None else data.commission_max
        hi = data.commission_max if data.commission_max is not None else lo
        commission = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
        if data.commission_unit:
            commission += data.commission_unit
    return (
        f"[手动录入] {data.work_summary} | 日期={data.work_date.isoformat()} "
        f"时段={data.work_time or '未知'} | 佣金={commission or '未知'} | "
        f"地点={data.location or '未知'} | 联系={data.contact or '未知'}"
    )


async def create_manual_offer(pool: AsyncConnectionPool, data: ManualOfferInput) -> dict:
    """手动录入兼职（直接 INSERT，不经大模型）"""
    raw_text = _build_manual_raw_text(data)
    content_hash = hashlib.sha256(f"manual_form:{uuid4().hex}".encode()).hexdigest()[:16]
    offer_status = "todo" if data.is_todo else "favorite"
    commission_text = raw_text.split("佣金=")[1].split(" |")[0]

    sql = f"""
        INSERT INTO jobs (
            group_name, source, source_ref, raw_text, content_hash, is_valid_job,
            work_summary, work_date, work_time,
            commission_min, commission_max, commission_unit, commission_text,
            location, contact, notes, physical_intensity,
            offer_status, offer_updated_at
        ) VALUES (
            '手动录入', 'manual_form', 'manual_form', %s, %s, TRUE,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, now()
        )
        RETURNING {_COLUMNS_SQL}
    """
    params = (
        raw_text, content_hash,
        data.work_summary, data.work_date, data.work_time,
        data.commission_min, data.commission_max, data.commission_unit, commission_text,
        data.location, data.contact, data.notes, data.physical_intensity,
        offer_status,
    )
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        columns = [c.name for c in cur.description]
        row = convert_job_row(columns, await cur.fetchone())
    logger.info("手动录入兼职 id={} summary={!r}", row["id"], data.work_summary)

    #3.单条计算通勤（高德 MCP；失败不影响录入）
    if row.get("location"):
        try:
            from agent.commute_service import annotate_and_persist_commute

            ok_count, _ = await annotate_and_persist_commute(
                pool, [{"id": row["id"], "location": row.get("location")}]
            )
            if ok_count:
                refreshed = await get_offers_by_ids(pool, [row["id"]])
                if refreshed:
                    return refreshed[0]
        except Exception:
            logger.exception("手动录入通勤计算异常（不影响录入）")
    return enrich_row(row)


async def update_offer(
    pool: AsyncConnectionPool, job_id: int, data: OfferUpdateInput
) -> dict | None:
    """编辑兼职白名单字段（不存在返回 None）"""
    fields = data.model_dump(exclude_unset=True)
    assignments = [f"{name} = %s" for name in _UPDATE_FIELDS if name in fields]
    if not assignments:
        return (await get_offers_by_ids(pool, [job_id]) or [None])[0]

    values = [fields[name] for name in _UPDATE_FIELDS if name in fields]
    sql = f"""
        UPDATE jobs SET {", ".join(assignments)}, offer_updated_at = now()
        WHERE id = %s
        RETURNING {_COLUMNS_SQL}
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (*values, job_id))
        row = await cur.fetchone()
        if row is None:
            return None
        columns = [c.name for c in cur.description]
    return enrich_row(convert_job_row(columns, row))


async def delete_offer(pool: AsyncConnectionPool, job_id: int) -> tuple[bool, str]:
    """删除/移除兼职

    - source='manual_form'：硬删除（用户自建记录，无血缘损失）
    - 其他来源：软移除（offer_status='none'，保留 jobs 行与去重约束）

    Returns:
        (是否删除到记录, "hard" | "soft" | "")
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT source FROM jobs WHERE id = %s", (job_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return False, ""
        if row[0] == "manual_form":
            await conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
            logger.info("手动录入已硬删除 id={}", job_id)
            return True, "hard"
        await conn.execute(
            "UPDATE jobs SET offer_status = 'none', offer_updated_at = NULL WHERE id = %s",
            (job_id,),
        )
        logger.info("已从心动的offer移除（软删） id={}", job_id)
        return True, "soft"