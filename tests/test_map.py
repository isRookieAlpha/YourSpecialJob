# -*- coding: utf-8 -*-
"""静态地图测试（全部 mock HTTP，不真实调用高德）。"""
from types import SimpleNamespace

import pytest

import agent.amap_map as map_mod

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._json


class FakeClient:
    """模拟高德 geocode/staticmap"""

    def __init__(self, geo_map: dict, png: bytes = PNG, static_json: dict | None = None):
        self.geo_map = geo_map
        self.png = png
        self.static_json = static_json
        self.geo_calls: list[str] = []
        self.static_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        if "geocode" in url:
            address = params["address"]
            self.geo_calls.append(address)
            loc = self.geo_map.get(address)
            if loc is None:
                return FakeResponse({"status": "0", "info": "INVALID_USER_KEY", "geocodes": []})
            return FakeResponse({"status": "1", "geocodes": [{"location": loc}]})
        self.static_calls += 1
        if self.static_json is not None:
            return FakeResponse(self.static_json, content=b'{"status":"0","info":"INVALID_USER_KEY"}')
        return FakeResponse(content=self.png)


@pytest.fixture(autouse=True)
def clean_cache():
    map_mod.clear_cache()
    yield
    map_mod.clear_cache()


def patch_env(monkeypatch, client: FakeClient, key: str = "test-key", city: str = ""):
    monkeypatch.setattr(map_mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))
    monkeypatch.setattr(
        map_mod, "get_settings",
        lambda: SimpleNamespace(amap_server_key=key, preferences={"amap_city": city} if city else {}),
    )


GEO = {"静安寺": "121.445,31.223", "松江": "121.227,31.032"}


class TestGeocode:
    async def test_success(self, monkeypatch):
        client = FakeClient(GEO)
        patch_env(monkeypatch, client, city="上海")
        async with client as c:
            assert await map_mod.geocode(c, "静安寺", "test-key", "上海") == "121.445,31.223"

    async def test_cache_hit_avoids_http(self, monkeypatch):
        client = FakeClient(GEO)
        patch_env(monkeypatch, client)
        async with client as c:
            await map_mod.geocode(c, "静安寺", "k")
            assert await map_mod.geocode(c, "静安寺", "k") == "121.445,31.223"
        assert client.geo_calls.count("静安寺") == 1

    async def test_miss_cached(self, monkeypatch):
        client = FakeClient(GEO)
        patch_env(monkeypatch, client)
        async with client as c:
            assert await map_mod.geocode(c, "不存在的地方", "k") is None
            assert await map_mod.geocode(c, "不存在的地方", "k") is None
        assert client.geo_calls.count("不存在的地方") == 1


class TestBuildStaticMap:
    async def test_key_missing(self, monkeypatch):
        patch_env(monkeypatch, FakeClient(GEO), key="")
        with pytest.raises(RuntimeError, match="未配置高德"):
            await map_mod.build_static_map(["静安寺"])

    async def test_no_addresses(self, monkeypatch):
        patch_env(monkeypatch, FakeClient(GEO))
        with pytest.raises(RuntimeError, match="没有可展示"):
            await map_mod.build_static_map(["", "  "])

    async def test_success_png(self, monkeypatch):
        client = FakeClient(GEO)
        patch_env(monkeypatch, client)
        png, skipped, located = await map_mod.build_static_map(["静安寺", "松江", "静安寺"])
        assert png == PNG
        assert skipped == 0
        assert located == ["静安寺", "松江"]  # 顺序即 markers A/B 标签
        assert client.static_calls == 1

    async def test_partial_failure_skipped(self, monkeypatch):
        client = FakeClient({"静安寺": "121.445,31.223"})
        patch_env(monkeypatch, client)
        png, skipped, located = await map_mod.build_static_map(["静安寺", "找不到的地方"])
        assert skipped == 1

    async def test_all_fail(self, monkeypatch):
        patch_env(monkeypatch, FakeClient({}))
        with pytest.raises(RuntimeError, match="暂不可用"):
            await map_mod.build_static_map(["找不到的地方"])

    async def test_staticmap_returns_json_error(self, monkeypatch):
        """高德 200 + JSON 错误 → 校验 PNG 魔数后抛错"""
        client = FakeClient(GEO, static_json={"status": "0", "info": "INVALID_USER_KEY"})
        patch_env(monkeypatch, client)
        with pytest.raises(RuntimeError, match="地图服务暂不可用"):
            await map_mod.build_static_map(["静安寺"])

    async def test_marker_limit_10(self, monkeypatch):
        geo = {f"地点{i}": f"121.{i},31.{i}" for i in range(15)}
        client = FakeClient(geo)
        patch_env(monkeypatch, client)
        _, skipped, located = await map_mod.build_static_map([f"地点{i}" for i in range(15)])
        assert skipped == 5  # 只 geocode/展示前 10 个

    async def test_error_message_has_no_key(self, monkeypatch):
        client = FakeClient({}, static_json={"status": "0", "info": "ERR"})
        patch_env(monkeypatch, client, key="super-secret-key")
        with pytest.raises(RuntimeError) as exc:
            await map_mod.build_static_map(["静安寺"])
        assert "super-secret-key" not in str(exc.value)

async def test_geocode_info_returns_adcode(monkeypatch):
    """geocode_info 同时返回 location 与 adcode（天气接口需要 adcode）"""

    class Resp:
        def json(self):
            return {"status": "1", "geocodes": [{"location": "121.4,31.2", "adcode": "310106"}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            return Resp()

    monkeypatch.setattr(map_mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: Client()))
    map_mod.clear_cache()
    async with Client() as c:
        info = await map_mod.geocode_info(c, "静安寺", "k")
    assert info == {"location": "121.4,31.2", "adcode": "310106"}
