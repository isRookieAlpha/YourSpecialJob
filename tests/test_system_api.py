# -*- coding: utf-8 -*-
"""系统辅助接口测试：区划代理 + 目录选择。"""
import backend.routers.system as system_mod


async def test_districts_proxy(api_client, monkeypatch):
    async def fake_fetch(adcode="100000"):
        return [{"name": "上海市", "adcode": "310000", "level": "province", "center": ""}]

    monkeypatch.setattr(system_mod, "fetch_districts", fake_fetch)
    resp = await api_client.get("/api/system/districts")
    assert resp.status_code == 200
    assert resp.json()["districts"][0]["name"] == "上海市"


async def test_districts_missing_key_503(api_client, monkeypatch):
    async def fake_fetch(adcode="100000"):
        raise RuntimeError("未配置高德 Web 服务 key，无法加载省市区")

    monkeypatch.setattr(system_mod, "fetch_districts", fake_fetch)
    resp = await api_client.get("/api/system/districts")
    assert resp.status_code == 503


async def test_districts_upstream_error_502(api_client, monkeypatch):
    async def fake_fetch(adcode="100000"):
        raise RuntimeError("高德区划查询失败：INVALID_USER_KEY")

    monkeypatch.setattr(system_mod, "fetch_districts", fake_fetch)
    resp = await api_client.get("/api/system/districts")
    assert resp.status_code == 502


async def test_pick_directory(api_client, monkeypatch):
    monkeypatch.setattr(system_mod, "_pick_directory_blocking", lambda: "C:\\Users\\me\\Desktop")
    resp = await api_client.post("/api/system/pick-directory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "C:\\Users\\me\\Desktop"
    assert data["cancelled"] is False


async def test_pick_directory_cancelled(api_client, monkeypatch):
    monkeypatch.setattr(system_mod, "_pick_directory_blocking", lambda: None)
    resp = await api_client.post("/api/system/pick-directory")
    assert resp.status_code == 200
    assert resp.json() == {"path": None, "cancelled": True}


async def test_pick_directory_busy_409(api_client, monkeypatch):
    assert system_mod._picker_lock.acquire(blocking=False)
    try:
        resp = await api_client.post("/api/system/pick-directory")
        assert resp.status_code == 409
    finally:
        system_mod._picker_lock.release()


async def test_pick_directory_unavailable_501(api_client, monkeypatch):
    def fail():
        raise RuntimeError("当前环境不支持系统目录选择，请手动输入路径")

    monkeypatch.setattr(system_mod, "_pick_directory_blocking", fail)
    resp = await api_client.post("/api/system/pick-directory")
    assert resp.status_code == 501