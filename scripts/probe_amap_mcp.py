# -*- coding: utf-8 -*-
"""
高德 MCP 探针：真实启动 @amap/amap-maps-mcp-server，列工具并计算一条通勤。

验证：
1. npx stdio 启动与持久会话可用
2. maps_geo / maps_bicycling / maps_direction_transit_integrated / maps_direction_driving 返回结构
3. 打印四项通勤原始数据（key 不输出）

用法：python scripts/probe_amap_mcp.py [起点] [终点] [城市]
默认：上海普陀区曹阳二村163号 → 静安寺，上海
"""
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import get_settings  # noqa: E402


def preview(data, limit: int = 600) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text[:limit] + ("…" if len(text) > limit else "")


async def main() -> int:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    key = get_settings().amap_server_key
    if not key:
        print("未配置高德 Web 服务 key")
        return 2

    origin_addr = sys.argv[1] if len(sys.argv) > 1 else "上海普陀区曹阳二村163号"
    dest_addr = sys.argv[2] if len(sys.argv) > 2 else "静安寺"
    city = sys.argv[3] if len(sys.argv) > 3 else "上海"

    client = MultiServerMCPClient({
        "amap": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {"AMAP_MAPS_API_KEY": key},
        }
    })

    print("启动 MCP 会话（首次需下载 npm 包，可能较慢）…")
    async with client.session("amap") as session:
        tools = await load_mcp_tools(session)
        names = sorted(t.name for t in tools)
        print(f"工具（{len(names)}）：{names}")
        by_name = {t.name: t for t in tools}

        async def call(name, args):
            result = await asyncio.wait_for(by_name[name].ainvoke(args), timeout=90)
            if isinstance(result, list):
                text = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in result
                )
            else:
                text = str(result)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                print(f"[{name}] 非 JSON 返回：{text[:300]}")
                return {}

        geo_o = await call("maps_geo", {"address": origin_addr, "city": city})
        print(f"\nmaps_geo 起点：{preview(geo_o)}")
        o = (geo_o.get("return") or [{}])[0].get("location")
        geo_d = await call("maps_geo", {"address": dest_addr, "city": city})
        print(f"maps_geo 终点：{preview(geo_d)}")
        d = (geo_d.get("return") or [{}])[0].get("location")
        if not o or not d:
            print("地理编码失败")
            return 1

        bike = await call("maps_bicycling", {"origin": o, "destination": d})
        print(f"\nmaps_bicycling：{preview(bike)}")
        transit = await call("maps_direction_transit_integrated", {
            "origin": o, "destination": d, "city": city, "cityd": city,
        })
        print(f"\nmaps_direction_transit_integrated：{preview(transit)}")
        driving = await call("maps_direction_driving", {"origin": o, "destination": d})
        print(f"\nmaps_direction_driving：{preview(driving)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))