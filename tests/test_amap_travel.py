# -*- coding: utf-8 -*-
"""高德骑行/天气测试（全部 mock HTTP）。"""
from types import SimpleNamespace

import pytest

import agent.amap_travel as travel_mod


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, bike_data=None, weather_data=None):
        self.bike_data = bike_data or {}
        self.weather_data = weather_data or {}
        self.bike_calls = 0
        self.weather_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        if "bicycling" in url:
            self.bike_calls += 1
            return FakeResponse(self.bike_data)
        self.weather_calls += 1
        return FakeResponse(self.weather_data)


@pytest.fixture(autouse=True)
def clean_cache():
    travel_mod.clear_cache()
    yield
    travel_mod.clear_cache()


def patch_env(monkeypatch, client, key="test-key", city=""):
    monkeypatch.setattr(travel_mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))
    monkeypatch.setattr(
        travel_mod, "get_settings",
        lambda: SimpleNamespace(
            amap_server_key=key,
            preferences={"amap_city": city} if city else {},
        ),
    )


BIKE_OK = {
    "errcode": 0,
    "route": {"paths": [{"distance": "4200", "cost": {"duration": "900"}}]},
}
WEATHER_OK = {
    "status": "1",
    "lives": [{
        "city": "上海市", "weather": "多云", "temperature": "18",
        "windpower": "2", "reporttime": "2026-11-05 10:00:00",
    }],
}


class TestBicyclingRoute:
    async def test_parse_duration(self):
        client = FakeClient(bike_data=BIKE_OK)
        async with client as c:
            route = await travel_mod.bicycling_route(c, "121.1,31.1", "121.2,31.2", "k")
        assert route.distance_m == 4200
        assert route.duration_min == 15

    async def test_fallback_path_duration(self):
        data = {"errcode": 0, "route": {"paths": [{"distance": "600", "duration": "120"}]}}
        client = FakeClient(bike_data=data)
        async with client as c:
            route = await travel_mod.bicycling_route(c, "a", "b", "k")
        assert route.duration_min == 2

    async def test_error_returns_none(self):
        client = FakeClient(bike_data={"errcode": 20003, "errmsg": "INVALID_USER_KEY"})
        async with client as c:
            assert await travel_mod.bicycling_route(c, "a", "b", "super-secret") is None

    async def test_cache_avoids_http(self):
        client = FakeClient(bike_data=BIKE_OK)
        async with client as c:
            await travel_mod.bicycling_route(c, "a", "b", "k")
            await travel_mod.bicycling_route(c, "a", "b", "k")
        assert client.bike_calls == 1


class TestWeatherToday:
    async def test_adcode_direct(self, monkeypatch):
        client = FakeClient(weather_data=WEATHER_OK)
        patch_env(monkeypatch, client)
        weather = await travel_mod.weather_today("310106")
        assert weather.weather == "多云"
        assert weather.temperature == "18"
        assert client.weather_calls == 1

    async def test_city_name_resolved(self, monkeypatch):
        client = FakeClient(weather_data=WEATHER_OK)
        patch_env(monkeypatch, client)

        async def fake_geo(c, address, key, city=""):
            return {"location": "121.4,31.2", "adcode": "310000"}

        monkeypatch.setattr(travel_mod, "geocode_info", fake_geo)
        weather = await travel_mod.weather_today("上海")
        assert weather.city == "上海市"

    async def test_missing_key_returns_none(self, monkeypatch):
        patch_env(monkeypatch, FakeClient(), key="")
        assert await travel_mod.weather_today("上海") is None

    async def test_error_returns_none(self, monkeypatch):
        client = FakeClient(weather_data={"status": "0", "info": "INVALID_USER_KEY"})
        patch_env(monkeypatch, client)
        assert await travel_mod.weather_today("310106") is None

    async def test_cache(self, monkeypatch):
        client = FakeClient(weather_data=WEATHER_OK)
        patch_env(monkeypatch, client)
        await travel_mod.weather_today("310106")
        await travel_mod.weather_today("310106")
        assert client.weather_calls == 1


class TestAnnotateCommute:
    async def test_no_origin_marks_unknown(self, monkeypatch):
        patch_env(monkeypatch, FakeClient())
        rows = [{"id": 1, "location": "静安寺"}]
        result = await travel_mod.annotate_commute(rows, "", "", 30)
        assert result[0]["commute_unknown"] is True

    async def test_success_and_over_limit(self, monkeypatch):
        client = FakeClient(bike_data=BIKE_OK)
        patch_env(monkeypatch, client)

        async def fake_geo(c, address, key, city=""):
            return {"location": f"121.{len(address)},31.1", "adcode": "310000"}

        monkeypatch.setattr(travel_mod, "geocode_info", fake_geo)
        rows = [{"id": 1, "location": "静安寺", "work_summary": "传菜"}]
        result = await travel_mod.annotate_commute(rows, "人民广场", "上海", 10)
        assert result[0]["commute_bike_min"] == 15
        assert result[0]["commute_over_limit"] is True  # 15 > 10

    async def test_geocode_failure_fail_open(self, monkeypatch):
        patch_env(monkeypatch, FakeClient())

        async def no_geo(c, address, key, city=""):
            return None

        monkeypatch.setattr(travel_mod, "geocode_info", no_geo)
        rows = [{"id": 1, "location": "未知地点"}]
        result = await travel_mod.annotate_commute(rows, "起点", "上海", 30)
        assert result[0]["commute_unknown"] is True
        assert result[0]["commute_over_limit"] is False