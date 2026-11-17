# -*- coding: utf-8 -*-
"""高德区划查询测试（mock HTTP，无网络）。"""
from types import SimpleNamespace

import pytest

import agent.amap_district as district_mod
from agent.amap_district import clear_cache, fetch_districts

DISTRICT_DATA = {
    "status": "1",
    "districts": [{
        "name": "中国",
        "adcode": "100000",
        "districts": [
            {"name": "上海市", "adcode": "310000", "level": "province", "center": "121.47,31.23"},
            {"name": "江苏省", "adcode": "320000", "level": "province", "center": "118.78,32.04"},
        ],
    }],
}


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def get(self, url, params=None):
        self.calls += 1
        return FakeResponse(self.data)


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    clear_cache()
    monkeypatch.setattr(
        district_mod, "get_settings",
        lambda: SimpleNamespace(amap_server_key="test-key"),
    )
    yield
    clear_cache()


class TestFetchDistricts:
    async def test_parses_children(self):
        client = FakeClient(DISTRICT_DATA)
        result = await fetch_districts("100000", client=client)
        assert [d["name"] for d in result] == ["上海市", "江苏省"]
        assert result[0]["adcode"] == "310000"
        assert result[0]["level"] == "province"
        assert client.calls == 1

    async def test_cache_hit(self):
        client = FakeClient(DISTRICT_DATA)
        await fetch_districts("100000", client=client)
        await fetch_districts("100000", client=client)
        assert client.calls == 1

    async def test_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(
            district_mod, "get_settings",
            lambda: SimpleNamespace(amap_server_key=""),
        )
        with pytest.raises(RuntimeError, match="未配置"):
            await fetch_districts("100000", client=FakeClient(DISTRICT_DATA))

    async def test_upstream_error(self):
        client = FakeClient({"status": "0", "info": "INVALID_USER_KEY"})
        with pytest.raises(RuntimeError, match="INVALID_USER_KEY"):
            await fetch_districts("100000", client=client)

    async def test_empty_children(self):
        client = FakeClient({"status": "1", "districts": [{"districts": []}]})
        assert await fetch_districts("310000", client=client) == []