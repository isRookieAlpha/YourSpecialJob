# -*- coding: utf-8 -*-
"""
补算通勤：给已入库但通勤未计算的兼职重新调用高德 MCP。

用法：
    python scripts/backfill_commute.py            # 补算全部缺通勤的记录
    python scripts/backfill_commute.py --limit 50 # 最多补算 N 条
    python scripts/backfill_commute.py --all      # 包括通勤已存在但想重算的记录

依赖 preferences.commute_origin / amap_city（设置页配置）。
"""
import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.commute_service import annotate_and_persist_commute  # noqa: E402
from agent.config import get_settings  # noqa: E402
from agent.db.pool import close_pool, get_pool  # noqa: E402
from agent.db.settings_dao import apply_overrides, get_raw_settings  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="补算兼职通勤（高德 MCP）")
    parser.add_argument("--limit", type=int, default=500, help="最多处理条数")
    parser.add_argument("--all", action="store_true", help="包含已有通勤的记录")
    args = parser.parse_args()

    pool = await get_pool()
    # 独立进程只读 .env，需手动应用 DB 覆盖（否则读不到设置页的通勤起点/城市）
    db_data = await get_raw_settings(pool)
    if db_data:
        get_settings.cache_clear()
        apply_overrides(get_settings(), db_data)
    print(f"通勤起点：{get_settings().preferences.get('commute_origin')!r} "
          f"城市：{get_settings().preferences.get('amap_city')!r}")

    condition = "" if args.all else "AND commute_bike_min IS NULL AND commute_updated_at IS NULL"
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""SELECT id, location FROM jobs
                WHERE location IS NOT NULL AND trim(location) <> '' {condition}
                ORDER BY id LIMIT %s""",
            (args.limit,),
        )
        rows = [{"id": r[0], "location": r[1]} for r in await cur.fetchall()]

    if not rows:
        print("没有需要补算通勤的兼职")
        await close_pool()
        return 0

    print(f"待补算 {len(rows)} 条（地点去重后计算）…")
    ok, mcp_failed = await annotate_and_persist_commute(pool, rows)
    if mcp_failed:
        print("高德 MCP 不可用，本次未计算任何通勤（详见日志）")
    else:
        print(f"补算完成：成功写入 {ok} 条 / 共 {len(rows)} 条（地址无法定位的会跳过）")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*), count(commute_bike_min) FROM jobs"
        )
        total, with_commute = await cur.fetchone()
    print(f"当前 jobs：{total} 条，其中有通勤数据 {with_commute} 条")
    await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))