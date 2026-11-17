# -*- coding: utf-8 -*-
"""高德行政区划查询（省/市/区三级联动；后端代理 + TTL 缓存）。

只返回 name/adcode/level/center，key 不进入响应与日志。
"""
import time
from collections import OrderedDict

import httpx
from loguru import logger

from agent.config import get_settings

DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
DISTRICT_TTL_SEC = 24 * 3600
DISTRICT_CACHE_MAX = 128
ROOT_ADCODE = "100000"  # 中国

#1.模块级缓存：adcode -> (expire_ts, districts)
_cache: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()


def clear_cache() -> None:
    """清空缓存（测试用）"""
    _cache.clear()


def _normalize(item: dict) -> dict:
    return {
        "name": str(item.get("name") or ""),
        "adcode": str(item.get("adcode") or ""),
        "level": str(item.get("level") or ""),
        "center": str(item.get("center") or ""),
    }


async def fetch_districts(
    parent_adcode: str = ROOT_ADCODE,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """查询下级行政区（subdistrict=1）

    Raises:
        RuntimeError: 未配置 key 或高德返回错误
    """
    key = get_settings().amap_server_key
    if not key:
        raise RuntimeError("未配置高德 Web 服务 key，无法加载省市区")

    adcode = str(parent_adcode or ROOT_ADCODE).strip() or ROOT_ADCODE
    now = time.time()
    cached = _cache.get(adcode)
    if cached and cached[0] >= now:
        _cache.move_to_end(adcode)
        return [dict(item) for item in cached[1]]

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=5)
    try:
        resp = await client.get(
            DISTRICT_URL,
            params={
                "key": key,
                "keywords": adcode,
                "subdistrict": 1,
                "extensions": "base",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"高德区划查询失败：{e}") from e
    finally:
        if owns_client:
            await client.aclose()

    if str(data.get("status")) != "1":
        raise RuntimeError(f"高德区划查询失败：{data.get('info') or '未知错误'}")

    districts = data.get("districts") or []
    children = (districts[0].get("districts") or []) if districts else []
    result = [_normalize(item) for item in children if item.get("name")]

    _cache[adcode] = (now + DISTRICT_TTL_SEC, result)
    _cache.move_to_end(adcode)
    while len(_cache) > DISTRICT_CACHE_MAX:
        _cache.popitem(last=False)
    logger.info("区划已加载 adcode={} 下级 {} 个", adcode, len(result))
    return [dict(item) for item in result]