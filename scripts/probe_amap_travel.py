# -*- coding: utf-8 -*-
"""
高德出行探针：真实计算骑行路线与今日天气（key 已脱敏）。

用法：python scripts/probe_amap_travel.py [起点] [终点] [城市]
默认：人民广场 → 静安寺，城市上海。
"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent.amap_map import geocode_info  # noqa: E402
from agent.amap_travel import bicycling_route, weather_today  # noqa: E402
from agent.config import get_settings  # noqa: E402


async def main() -> int:
    key = get_settings().amap_server_key
    if not key:
        print("未配置高德 Web 服务 key（AMAP_SERVER_KEY / AMAP_MAPS_API_KEY）")
        return 2

    origin = sys.argv[1] if len(sys.argv) > 1 else "人民广场"
    destination = sys.argv[2] if len(sys.argv) > 2 else "静安寺"
    city = sys.argv[3] if len(sys.argv) > 3 else "上海"

    async with httpx.AsyncClient(timeout=5) as client:
        o = await geocode_info(client, origin, key, city)
        d = await geocode_info(client, destination, key, city)
        print(f"起点：{origin} → {o}")
        print(f"终点：{destination} → {d}")
        if o and d:
            route = await bicycling_route(client, o["location"], d["location"], key)
            print(f"骑行：{route}")

    weather = await weather_today(city)
    print(f"天气：{weather}")
    return 0 if o and d else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))