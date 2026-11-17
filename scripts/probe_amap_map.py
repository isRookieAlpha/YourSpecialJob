# -*- coding: utf-8 -*-
"""
高德静态地图探针：geocode 地址 → staticmap 下载 PNG（验证 key 与接口可用）。

用法：python scripts/probe_amap_map.py [地址1] [地址2] ...
默认测试地点：静安寺、松江。
输出：logs/probe_map.png（key 已脱敏打印，不泄露到日志/错误）。
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent.config import get_settings  # noqa: E402

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
STATIC_URL = "https://restapi.amap.com/v3/staticmap"


def mask_key(url: str, key: str) -> str:
    """打印用 URL 脱敏（key 替换为 ***）"""
    return url.replace(key, "***") if key else url


async def geocode(client: httpx.AsyncClient, address: str, key: str, city: str = "") -> str | None:
    params = {"key": key, "address": address}
    if city:
        params["city"] = city
    r = await client.get(GEOCODE_URL, params=params)
    data = r.json()
    geos = data.get("geocodes") or []
    if not geos:
        print(f"  geocode 失败：{data.get('info')} {data.get('infocode')}")
        return None
    loc = geos[0].get("location")
    print(f"  geocode OK：{address} → {loc}（{geos[0].get('formatted_address', '')}）")
    return loc


async def main() -> int:
    settings = get_settings()
    key = settings.amap_server_key
    if not key:
        print("未配置高德 Web 服务 key（AMAP_SERVER_KEY / AMAP_MAPS_API_KEY）")
        return 2
    addresses = sys.argv[1:] or ["静安寺", "松江"]
    city = (settings.preferences or {}).get("amap_city", "")

    async with httpx.AsyncClient(timeout=10) as client:
        coords = []
        for addr in addresses:
            loc = await geocode(client, addr, key, city)
            if loc:
                coords.append((addr, loc))

        if not coords:
            print("所有地址均无法解析坐标")
            return 1

        #1.markers：mid 尺寸显示 label；最多 10 个
        labels = "ABCDEFGHIJ"
        markers = "|".join(
            f"mid,0x2563EB,{labels[i]}:{loc}" for i, (_, loc) in enumerate(coords[:10])
        )
        params = {"key": key, "size": "750*360", "scale": "1", "markers": markers}
        r = await client.get(STATIC_URL, params=params)
        print(f"\nstaticmap 状态：{r.status_code} Content-Type={r.headers.get('content-type')}")
        print(f"脱敏请求：{mask_key(str(r.request.url), key)}")

        #2.高德会 200 + JSON 错误，校验 PNG 魔数
        if r.content[:8] != b"\x89PNG\r\n\x1a\n":
            print(f"非 PNG 响应：{r.text[:300]}")
            return 1

        out = ROOT / "logs" / "probe_map.png"
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(r.content)
        print(f"PNG 已保存：{out}（{len(r.content)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))