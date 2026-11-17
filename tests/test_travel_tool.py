# -*- coding: utf-8 -*-
"""get_today_weather 工具测试（mock 底层天气，不真实调用高德）。"""
from types import SimpleNamespace

import agent.tools.travel as tool_mod
from agent.tools.travel import get_today_weather


class FakeWeather:
    city = "上海市"
    weather = "小雨"
    temperature = "15"
    windpower = "3"


def patch_deps(monkeypatch, prefs, weather=None):
    async def fake_weather(city=None):
        return weather

    monkeypatch.setattr(tool_mod, "weather_today", fake_weather)
    monkeypatch.setattr(tool_mod, "get_settings", lambda: SimpleNamespace(preferences=prefs))


class TestWeatherTool:
    async def test_weather_ok(self, monkeypatch):
        patch_deps(monkeypatch, {"amap_city": "上海"}, weather=FakeWeather())
        text = await get_today_weather.ainvoke({})
        assert "今日天气（上海市）：小雨 15℃" in text

    async def test_city_argument_overrides(self, monkeypatch):
        seen = {}

        async def fake_weather(city=None):
            seen["city"] = city
            return FakeWeather()

        monkeypatch.setattr(tool_mod, "weather_today", fake_weather)
        monkeypatch.setattr(tool_mod, "get_settings", lambda: SimpleNamespace(preferences={"amap_city": "上海"}))
        await get_today_weather.ainvoke({"city": "北京"})
        assert seen["city"] == "北京"

    async def test_no_city(self, monkeypatch):
        patch_deps(monkeypatch, {})
        text = await get_today_weather.ainvoke({})
        assert "未配置地图城市" in text

    async def test_unavailable(self, monkeypatch):
        patch_deps(monkeypatch, {"amap_city": "上海"}, weather=None)
        text = await get_today_weather.ainvoke({})
        assert "今日天气：不可用" in text

    async def test_exception_degrades(self, monkeypatch):
        async def failing(city=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(tool_mod, "weather_today", failing)
        monkeypatch.setattr(tool_mod, "get_settings", lambda: SimpleNamespace(preferences={"amap_city": "上海"}))
        text = await get_today_weather.ainvoke({})
        assert "查询失败" in text
        assert "请勿编造" in text