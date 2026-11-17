# -*- coding: utf-8 -*-
"""
高德静态地图：geocode（文本地址→坐标）+ staticmap（带标记点图片）。

安全与降级：
- key 只从 get_settings().amap_server_key 读取，不进入前端 URL/错误消息/日志
- geocode 双层缓存（命中 24h / 未命中 10min），并发限流 3，单个超时 5s
- 高德会返回 200 + JSON 错误，必须校验 Content-Type 与 PNG 魔数
- markers 上限 10 个；超出的地址计入 skipped（前端提示）
"""
import asyncio
import time
from collections import OrderedDict

import httpx
from loguru import logger

from agent.config import get_settings

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
STATIC_URL = "https://restapi.amap.com/v3/staticmap"

GEO_TTL_HIT = 24 * 3600
GEO_TTL_MISS = 10 * 60
GEO_CACHE_MAX = 256
MAX_MARKERS = 10
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAP_SIZE = "750*360"

#1.模块级 geocode 缓存：address -> (expire_ts, {"location": "lng,lat", "adcode": "310106"} | None)
_geo_cache: "OrderedDict[str, tuple[float, dict | None]]" = OrderedDict()


def _cache_get(address: str) -> tuple[bool, dict | None]:
    entry = _geo_cache.get(address)
    if entry is None or entry[0] < time.time():
        return False, None
    return True, entry[1]


def _cache_put(address: str, info: dict | None) -> None:
    ttl = GEO_TTL_HIT if info else GEO_TTL_MISS
    _geo_cache[address] = (time.time() + ttl, info)
    _geo_cache.move_to_end(address)
    while len(_geo_cache) > GEO_CACHE_MAX:
        _geo_cache.popitem(last=False)


def clear_cache() -> None:
    """清空缓存（测试/设置变更用）"""
    _geo_cache.clear()


async def geocode_info(
    client: httpx.AsyncClient, address: str, key: str, city: str = ""
) -> dict | None:
    """地址 → {"location": "lng,lat", "adcode": "城市编码"}（失败 None，带缓存）"""
    hit, info = _cache_get(address)
    if hit:
        return info

    params = {"key": key, "address": address}
    if city:
        params["city"] = city
    try:
        resp = await client.get(GEOCODE_URL, params=params)
        data = resp.json()
    except Exception as e:
        logger.warning("geocode 请求失败 address={!r}：{}", address, type(e).__name__)
        _cache_put(address, None)
        return None

    geos = data.get("geocodes") or []
    location = geos[0].get("location") if geos else None
    if not location or "," not in str(location):
        logger.info("geocode 未命中 address={!r} info={}", address, data.get("info"))
        _cache_put(address, None)
        return None
    info = {"location": str(location), "adcode": str(geos[0].get("adcode") or "")}
    _cache_put(address, info)
    return info


async def geocode(client: httpx.AsyncClient, address: str, key: str, city: str = "") -> str | None:
    """地址 → "lng,lat"（失败返回 None，带缓存）"""
    info = await geocode_info(client, address, key, city)
    return info["location"] if info else None


async def build_static_map(addresses: list[str]) -> tuple[bytes, int, list[str]]:
    """生成 ToDo 地点静态地图

    Args:
        addresses: 地点文本列表（自动去重去空）

    Returns:
        (PNG 字节, 未展示的地址数, 已定位地址列表（顺序与 markers 的 A/B/C 标签一致）)

    Raises:
        RuntimeError: key 未配置 / 无地点 / 全部 geocode 失败 / 地图服务异常（中文消息）
    """
    key = get_settings().amap_server_key
    if not key:
        raise RuntimeError("未配置高德 Web 服务 key（请在 .env 填写 AMAP_SERVER_KEY 或 AMAP_MAPS_API_KEY）")

    #2.去重去空（保序）
    seen: set[str] = set()
    unique: list[str] = []
    for addr in addresses:
        text = " ".join(str(addr or "").split())
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    if not unique:
        raise RuntimeError("没有可展示的 ToDo 地点")

    #3.超过 marker 上限的部分计入 skipped
    targets = unique[:MAX_MARKERS]
    skipped = len(unique) - len(targets)
    city = (get_settings().preferences or {}).get("amap_city", "")

    async with httpx.AsyncClient(timeout=5) as client:
        sem = asyncio.Semaphore(3)

        async def one(addr: str) -> tuple[str, str | None]:
            async with sem:
                return addr, await geocode(client, addr, key, city)

        results = await asyncio.gather(*(one(a) for a in targets))
        located = [(addr, coords) for addr, coords in results if coords]
        skipped += len(results) - len(located)
        if not located:
            raise RuntimeError("地图服务暂不可用或地点无法定位，请查看下方地点列表")

        #4.markers：mid 尺寸才显示 label（A-J）
        labels = "ABCDEFGHIJ"
        markers = "|".join(
            f"mid,0x2563EB,{labels[i]}:{coords}" for i, (_, coords) in enumerate(located)
        )
        params = {"key": key, "size": MAP_SIZE, "scale": "1", "markers": markers}
        try:
            resp = await client.get(STATIC_URL, params=params)
        except Exception as e:
            logger.warning("staticmap 请求失败：{}", type(e).__name__)
            raise RuntimeError("地图服务暂不可用，请查看下方地点列表") from e

    #5.高德可能 200 + JSON 错误：校验 PNG 魔数
    if resp.content[:8] != PNG_MAGIC:
        try:
            info = resp.json().get("info", "")
        except Exception:
            info = ""
        logger.warning("staticmap 返回非 PNG：status={} info={}", resp.status_code, info)
        raise RuntimeError(f"地图服务暂不可用（{info or '返回数据异常'}）")

    logger.info("静态地图生成：{} 个标记，{} 个跳过", len(located), skipped)
    return resp.content, skipped, [addr for addr, _ in located]