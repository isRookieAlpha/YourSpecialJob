# -*- coding: utf-8 -*-
"""高德 MCP 通勤计算测试（mock MCP，不启动 npx）。"""
import asyncio

import pytest

import agent.amap_mcp as mcp_mod
from agent.amap_mcp import AmapMcpError, _pick_city_location, _seconds_to_min, compute_commute_batch


class TestPickCityLocation:
    GEO = {
        "return": [
            {"city": "舟山市", "province": "浙江省", "location": "122.3,29.9"},
            {"city": "上海市", "province": "上海市", "location": "121.39,31.24"},
        ]
    }

    def test_filters_by_city(self):
        """MCP 忽略 city 参数，必须本地过滤（避免"静安寺→台州"歧义）"""
        assert _pick_city_location(self.GEO, "上海") == "121.39,31.24"

    def test_no_match_returns_none(self):
        assert _pick_city_location(self.GEO, "北京") is None

    def test_no_city_takes_first(self):
        assert _pick_city_location(self.GEO, "") == "122.3,29.9"

    def test_empty(self):
        assert _pick_city_location({}, "上海") is None

    def test_direct_municipality_city_name(self):
        """直辖市的高德区划名"上海城区"应匹配 geocode 的"上海市"（2026-11-07 线上问题）"""
        assert _pick_city_location(self.GEO, "上海城区") == "121.39,31.24"

    def test_city_core(self):
        from agent.amap_mcp import _city_core

        assert _city_core("上海城区") == "上海"
        assert _city_core("上海市") == "上海"
        assert _city_core("北京市") == "北京"
        assert _city_core("") == ""

    def test_geo_address_uses_core(self):
        from agent.amap_mcp import _geo_address

        assert _geo_address("上海城区", "普陀区曹阳二村163号") == "上海普陀区曹阳二村163号"
        assert _geo_address("上海城区", "上海市普陀区曹阳二村163号") == "上海市普陀区曹阳二村163号"

    def test_find_amap_error_unwraps_group(self):
        from agent.amap_mcp import _find_amap_error

        inner = AmapMcpError("通勤起点无法定位：x")
        group = ExceptionGroup("boom", [inner])
        assert _find_amap_error(group) is inner


class TestSecondsToMin:
    def test_convert(self):
        assert _seconds_to_min("900") == 15
        assert _seconds_to_min(60) == 1
        assert _seconds_to_min(0) is None
        assert _seconds_to_min(None) is None
        assert _seconds_to_min("abc") is None


class TestComputeCommuteBatch:
    async def test_success(self, monkeypatch):
        async def fake_compute(rows, origin, city, on_done=None, failures=None):
            return {
                rows[0]["id"]: {"distance_m": 3200, "bike_min": 14, "transit_min": 25, "drive_min": 18}
            }

        def fake_run(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(mcp_mod, "_compute_async", fake_compute)
        monkeypatch.setattr(mcp_mod, "_run_in_proactor_loop", fake_run)

        result = await compute_commute_batch([{"id": 1, "location": "静安寺"}], "起点", "上海")
        assert result[1]["bike_min"] == 14
        assert result[1]["transit_min"] == 25

    async def test_failures_passed_through(self, monkeypatch):
        """失败明细列表透传到 _compute_async（供设置页展示）"""
        async def fake_compute(rows, origin, city, on_done=None, failures=None):
            failures.append({"location": "x", "reason": "地址无法识别（高德无匹配结果）"})
            return {rows[0]["id"]: {"bike_min": 1}}

        def fake_run(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(mcp_mod, "_compute_async", fake_compute)
        monkeypatch.setattr(mcp_mod, "_run_in_proactor_loop", fake_run)

        failures: list[dict] = []
        result = await compute_commute_batch(
            [{"id": 1, "location": "x"}], "起点", "上海", failures=failures,
        )
        assert failures == [{"location": "x", "reason": "地址无法识别（高德无匹配结果）"}]
        assert result[1]["bike_min"] == 1

    async def test_empty_rows(self):
        assert await compute_commute_batch([], "起点", "上海") == {}

    async def test_mcp_error_propagates(self, monkeypatch):
        async def failing(rows, origin, city, on_done=None, failures=None):
            raise AmapMcpError("高德 MCP 未提供 maps_geo 工具")

        def fake_run(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(mcp_mod, "_compute_async", failing)
        monkeypatch.setattr(mcp_mod, "_run_in_proactor_loop", fake_run)
        with pytest.raises(AmapMcpError, match="maps_geo"):
            await compute_commute_batch([{"id": 1, "location": "x"}], "起点", "上海")

class TestMakeClient:
    """连接方式：Remote URL 优先；留空回退本地 npx；两者都没配置时报错"""

    def _patch(self, monkeypatch, settings):
        from types import SimpleNamespace  # noqa: F401

        captured: dict = {}

        class FakeClient:
            def __init__(self, connections):
                captured.update(connections)

        monkeypatch.setattr(mcp_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(
            "langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient
        )
        return captured

    def test_remote_url_preferred(self, monkeypatch):
        from types import SimpleNamespace

        captured = self._patch(
            monkeypatch, SimpleNamespace(amap_url="https://x/mcp", amap_server_key="key")
        )
        mcp_mod._make_client()
        assert captured["amap"] == {"transport": "http", "url": "https://x/mcp"}

    def test_fallback_to_stdio(self, monkeypatch):
        from types import SimpleNamespace

        captured = self._patch(
            monkeypatch, SimpleNamespace(amap_url="", amap_server_key="key")
        )
        mcp_mod._make_client()
        assert captured["amap"]["transport"] == "stdio"
        assert captured["amap"]["env"] == {"AMAP_MAPS_API_KEY": "key"}

    def test_missing_config_raises(self, monkeypatch):
        from types import SimpleNamespace

        self._patch(monkeypatch, SimpleNamespace(amap_url="", amap_server_key=""))
        with pytest.raises(AmapMcpError, match="Remote URL|AMAP_MAPS_API_KEY"):
            mcp_mod._make_client()
