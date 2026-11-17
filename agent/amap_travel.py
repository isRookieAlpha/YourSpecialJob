# -*- coding: utf-8 -*-
"""
高德出行能力：骑行路线规划 + 今日天气（推荐主流程与推送摘要使用）。

约束与约定：
- key 只从 get_settings().amap_server_key 读取，不进入日志/错误消息
- 失败一律降级返回 None（fail-open：通勤算不出来不阻止推荐）
- 缓存：骑行 6h（未命中 10min）、天气 30min（未命中 5min）
"""
import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx
from loguru import logger

from agent.amap_map import geocode_info
from agent.config import get_settings

BICYCLING_URL = "https://restapi.amap.com/v5/direction/bicycling"
WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

ROUTE_TTL_HIT = 6 * 3600
ROUTE_TTL_MISS = 10 * 60
WEATHER_TTL_HIT = 30 * 60
WEATHER_TTL_MISS = 5 * 60
ROUTE_CACHE_MAX = 512
WEATHER_CACHE_MAX = 64


@dataclass(frozen=True)
class BikeRoute:
    distance_m: int
    duration_min: int


@dataclass(frozen=True)
class WeatherToday:
    city: str
    weather: str
    temperature: str
    windpower: str
    reporttime: str


#1.进程内缓存
_route_cache: "OrderedDict[tuple[str, str], tuple[float, BikeRoute | None]]" = OrderedDict()
_weather_cache: "OrderedDict[str, tuple[float, WeatherToday | None]]" = OrderedDict()


def _cache_get(cache, key):
    entry = cache.get(key)
    if entry is None or entry[0] < time.time():
        return False, None
    cache.move_to_end(key)
    return True, entry[1]


def _cache_put(cache, key, value, ttl_hit: float, ttl_miss: float, max_size: int) -> None:
    ttl = ttl_hit if value is not None else ttl_miss
    cache[key] = (time.time() + ttl, value)
    cache.move_to_end(key)
    while len(cache) > max_size:
        cache.popitem(last=False)


def clear_cache() -> None:
    """清空缓存（测试用）"""
    _route_cache.clear()
    _weather_cache.clear()


async def bicycling_route(
    client: httpx.AsyncClient, origin_lnglat: str, dest_lnglat: str, key: str
) -> BikeRoute | None:
    """骑行路线（v5，duration 秒 → 分钟）；失败 None"""
    cache_key = (origin_lnglat, dest_lnglat)
    hit, cached = _cache_get(_route_cache, cache_key)
    if hit:
        return cached

    params = {
        "key": key,
        "origin": origin_lnglat,
        "destination": dest_lnglat,
        "show_fields": "cost",
    }
    try:
        resp = await client.get(BICYCLING_URL, params=params)
        data = resp.json()
    except Exception as e:
        logger.warning("骑行路线请求失败：{}", type(e).__name__)
        _cache_put(_route_cache, cache_key, None, ROUTE_TTL_HIT, ROUTE_TTL_MISS, ROUTE_CACHE_MAX)
        return None

    paths = ((data.get("route") or {}).get("paths") or []) if isinstance(data, dict) else []
    if data.get("errcode", data.get("status")) in (0, "1") and paths:
        path = paths[0]
        try:
            distance = int(float(path.get("distance") or 0))
            seconds = float((path.get("cost") or {}).get("duration") or path.get("duration") or 0)
            route = BikeRoute(distance_m=distance, duration_min=max(1, round(seconds / 60)))
        except (TypeError, ValueError):
            route = None
    else:
        route = None
        logger.info("骑行路线不可用 info={}", data.get("info") or data.get("errmsg"))

    _cache_put(_route_cache, cache_key, route, ROUTE_TTL_HIT, ROUTE_TTL_MISS, ROUTE_CACHE_MAX)
    return route


async def weather_today(city: str | None = None) -> WeatherToday | None:
    """今日实况天气（city 为城市名或 adcode）；失败 None"""
    key = get_settings().amap_server_key
    if not key:
        return None
    city_text = (city or (get_settings().preferences or {}).get("amap_city") or "").strip()
    if not city_text:
        return None

    async with httpx.AsyncClient(timeout=5) as client:
        #2.城市名 → adcode（6 位数字直接当 adcode）
        adcode = city_text
        if not city_text.isdigit():
            info = await geocode_info(client, city_text, key)
            adcode = (info or {}).get("adcode") or ""
            if not adcode:
                return None

        hit, cached = _cache_get(_weather_cache, adcode)
        if hit:
            return cached

        try:
            resp = await client.get(
                WEATHER_URL, params={"key": key, "city": adcode, "extensions": "base"}
            )
            data = resp.json()
        except Exception as e:
            logger.warning("天气请求失败：{}", type(e).__name__)
            _cache_put(_weather_cache, adcode, None, WEATHER_TTL_HIT, WEATHER_TTL_MISS, WEATHER_CACHE_MAX)
            return None

    lives = data.get("lives") or []
    if str(data.get("status")) != "1" or not lives:
        logger.info("天气不可用 info={}", data.get("info"))
        _cache_put(_weather_cache, adcode, None, WEATHER_TTL_HIT, WEATHER_TTL_MISS, WEATHER_CACHE_MAX)
        return None

    live = lives[0]
    weather = WeatherToday(
        city=live.get("city") or city_text,
        weather=live.get("weather") or "",
        temperature=live.get("temperature") or "",
        windpower=live.get("windpower") or "",
        reporttime=live.get("reporttime") or "",
    )
    _cache_put(_weather_cache, adcode, weather, WEATHER_TTL_HIT, WEATHER_TTL_MISS, WEATHER_CACHE_MAX)
    return weather


async def annotate_commute(
    rows: list[dict],
    origin_address: str,
    city: str,
    max_commute_min: int,
    limit: int = 5,
) -> list[dict]:
    """给前 limit 条候选计算骑行通勤（原地附加字段并返回 rows）

    附加字段：commute_bike_min / commute_distance_m / commute_over_limit / commute_unknown
    高德失败 fail-open：commute_unknown=True，不剔除候选。
    """
    for row in rows:
        row.setdefault("commute_bike_min", None)
        row.setdefault("commute_distance_m", None)
        row.setdefault("commute_over_limit", False)
        row.setdefault("commute_unknown", False)

    key = get_settings().amap_server_key
    origin = (origin_address or "").strip()
    if not key or not origin or not rows:
        for row in rows:
            row["commute_unknown"] = True
        return rows

    concrete = [r for r in rows[:limit] if (r.get("location") or "").strip()]
    if not concrete:
        for row in rows:
            row["commute_unknown"] = True
        return rows

    async with httpx.AsyncClient(timeout=5) as client:
        sem = asyncio.Semaphore(3)

        async def dest_info(location: str):
            async with sem:
                return location, await geocode_info(client, location, key, city)

        origin_info = await geocode_info(client, origin, key, city)
        if not origin_info:
            for row in rows:
                row["commute_unknown"] = True
            return rows
        origin_lnglat = origin_info["location"]

        #3.地点去重后计算（避免重复请求与配额浪费）
        unique_locations = list({(r.get("location") or "").strip() for r in concrete})
        located = await asyncio.gather(*(dest_info(loc) for loc in unique_locations))
        coords_by_location = {loc: info for loc, info in located if info}

        async def route_for(location: str):
            info = coords_by_location.get(location)
            if not info:
                return location, None
            async with sem:
                return location, await bicycling_route(client, origin_lnglat, info["location"], key)

        routes = dict(await asyncio.gather(*(route_for(loc) for loc in unique_locations)))

    for row in concrete:
        location = (row.get("location") or "").strip()
        route = routes.get(location)
        if route is None:
            row["commute_unknown"] = True
            continue
        row["commute_bike_min"] = route.duration_min
        row["commute_distance_m"] = route.distance_m
        row["commute_over_limit"] = route.duration_min > max_commute_min
    return rows