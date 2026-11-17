# -*- coding: utf-8 -*-
"""
通勤入库服务：新入库兼职 → 高德 MCP 计算（距离/骑行/公共交通/驾车）→ 写回 jobs。

用户确认：只用 MCP，不降级 REST；MCP 不可用时返回失败标记，
调用方在工具文本/入库统计中提示"高德 MCP 不可用，兼职通勤暂无法计算"。
"""
from datetime import datetime

from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.amap_mcp import AmapMcpError, compute_commute_batch
from agent.config import get_settings

_UPDATE_SQL = """
UPDATE jobs SET
    commute_distance_m = %s,
    commute_bike_min = %s,
    commute_transit_min = %s,
    commute_drive_min = %s,
    commute_updated_at = now()
WHERE id = %s
"""


async def list_commute_targets(pool: AsyncConnectionPool) -> list[dict]:
    """全库有工作地点的兼职（id + location，按 id 升序）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, location FROM jobs "
            "WHERE btrim(coalesce(location, '')) <> '' ORDER BY id"
        )
        rows = await cur.fetchall()
    return [{"id": row[0], "location": row[1]} for row in rows]


async def clear_commute_fields(pool: AsyncConnectionPool, job_ids: list[int]) -> int:
    """把指定兼职的通勤字段全部置 NULL（全库严格重算前调用）"""
    if not job_ids:
        return 0
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE jobs SET commute_distance_m = NULL, commute_bike_min = NULL, "
            "commute_metro_min = NULL, commute_transit_min = NULL, commute_drive_min = NULL, "
            "commute_updated_at = NULL WHERE id = ANY(%s)",
            (job_ids,),
        )
        return cur.rowcount


async def persist_commute(pool: AsyncConnectionPool, computed: dict[int, dict]) -> int:
    """把计算结果写回 jobs（返回写入条数）"""
    update_rows = [
        (
            data.get("distance_m"),
            data.get("bike_min"),
            data.get("transit_min"),
            data.get("drive_min"),
            job_id,
        )
        for job_id, data in computed.items()
    ]
    if not update_rows:
        return 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(_UPDATE_SQL, update_rows)
    return len(update_rows)


async def annotate_and_persist_commute(
    pool: AsyncConnectionPool, rows: list[dict]
) -> tuple[int, bool]:
    """给新入库的兼职计算通勤并写库

    Args:
        pool: 连接池
        rows: [{id, location}]（location 为空的自动跳过）

    Returns:
        (成功写入条数, MCP 是否不可用)
    """
    prefs = get_settings().preferences or {}
    origin = str(prefs.get("commute_origin") or "").strip()
    city = str(prefs.get("amap_city") or "").strip()

    targets = [r for r in rows if str(r.get("location") or "").strip()]
    if not origin or not targets:
        logger.info("通勤未计算：未配置起点或没有地点（origin={!r} rows={}）", origin, len(rows))
        return 0, False

    try:
        computed = await compute_commute_batch(targets, origin, city)
    except AmapMcpError as e:
        logger.warning("高德 MCP 不可用，兼职通勤暂无法计算：{}", e)
        return 0, True
    except Exception:
        logger.exception("通勤计算出现未预期异常")
        return 0, True

    updated = await persist_commute(pool, computed)
    if not updated:
        logger.info("通勤未计算：没有可定位的工作地点（{} 条）", len(targets))
        return 0, False

    logger.info(
        "通勤已写入：{} 条（起点 {} / 城市 {}，{}）",
        updated, origin, city or "未配置",
        datetime.now().strftime("%H:%M:%S"),
    )
    return updated, False