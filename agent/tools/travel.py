# -*- coding: utf-8 -*-
"""天气工具：查询今日实况天气（推荐时给室内外/出行建议）。

通勤数据在入库时已由高德 MCP 计算并存库（jobs.commute_*），不再由本工具计算。
"""
from langchain_core.tools import tool
from loguru import logger

from agent.amap_travel import weather_today
from agent.config import get_settings


@tool(parse_docstring=True)
async def get_today_weather(city: str | None = None) -> str:
    """查询今日实况天气（用于推荐时给出室内外或出行建议）。

    Args:
        city: 城市名（如 "上海"）；省略时使用设置页配置的地图城市。

    Returns:
        天气、气温、风力中文描述；不可用时如实说明（不要编造）。
    """
    prefs = get_settings().preferences or {}
    city_text = (city or str(prefs.get("amap_city") or "")).strip()
    if not city_text:
        return "今日天气：不可用（未配置地图城市）"
    try:
        weather = await weather_today(city_text)
    except Exception:
        logger.exception("天气查询失败")
        return "今日天气：查询失败，请勿编造天气信息"
    if weather is None:
        return "今日天气：不可用（高德服务异常或城市无法识别）"
    return (
        f"今日天气（{weather.city}）：{weather.weather} {weather.temperature}℃ "
        f"{weather.windpower}级风"
    )