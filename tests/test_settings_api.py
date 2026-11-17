# -*- coding: utf-8 -*-
"""设置 API 测试：脱敏 / PUT 语义 / 持久化 / 重建成功与回滚（mock rebuild_agent）。"""
import pytest

import backend.routers.settings as settings_router
from agent.db.settings_dao import get_raw_settings


@pytest.fixture
def rebuild_recorder(monkeypatch):
    """替换 rebuild_agent：记录调用，默认成功替换 app.state.agent"""
    calls = {"count": 0, "fail": False}

    async def fake_rebuild(app):
        calls["count"] += 1
        if calls["fail"]:
            raise RuntimeError("模拟构建失败")
        app.state.agent = object()

    monkeypatch.setattr(settings_router, "rebuild_agent", fake_rebuild)
    return calls


async def test_get_settings_masks_api_keys(api_client, rebuild_recorder):
    """GET 不返回明文 key，只返回 configured"""
    resp = await api_client.put("/api/settings", json={
        "strong": {"model": "m", "api_key": "sk-secret-123", "base_url": "u"},
    })
    assert resp.status_code == 200

    resp = await api_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "sk-secret-123" not in resp.text
    assert body["strong"]["api_key_configured"] is True
    assert body["strong"]["model"] == "m"


async def test_put_persists_to_db(api_client, pool, rebuild_recorder):
    """PUT 成功后写入 app_settings"""
    await api_client.put("/api/settings", json={"weak": {"model": "w2"}})
    data = await get_raw_settings(pool)
    assert data["weak"]["model"] == "w2"


async def test_empty_string_keeps_existing(api_client, pool, rebuild_recorder):
    """空串不修改"""
    await api_client.put("/api/settings", json={"weak": {"model": "w1", "base_url": "u1"}})
    await api_client.put("/api/settings", json={"weak": {"model": "", "base_url": "u2"}})
    data = await get_raw_settings(pool)
    assert data["weak"] == {"model": "w1", "base_url": "u2"}


async def test_null_clears_override(api_client, pool, rebuild_recorder):
    """显式 null 清除字段覆盖"""
    await api_client.put("/api/settings", json={"weak": {"model": "w1", "base_url": "u1"}})
    await api_client.put("/api/settings", json={"weak": {"model": None}})
    data = await get_raw_settings(pool)
    assert data["weak"] == {"base_url": "u1"}


async def test_unknown_field_rejected(api_client, rebuild_recorder):
    """未知字段 422"""
    resp = await api_client.put("/api/settings", json={"strong": {"apiKey": "x"}})
    assert resp.status_code == 422


async def test_rebuild_called_for_model_change(api_client, rebuild_recorder):
    """模型变更触发重建"""
    resp = await api_client.put("/api/settings", json={"weak": {"model": "w9"}})
    assert resp.status_code == 200
    assert resp.json()["agent_rebuilt"] is True
    assert rebuild_recorder["count"] == 1


async def test_preferences_only_skips_rebuild(api_client, rebuild_recorder):
    """仅偏好更新不重建 agent"""
    resp = await api_client.put("/api/settings", json={"preferences": {"commission_min": 100}})
    assert resp.status_code == 200
    assert resp.json()["agent_rebuilt"] is False
    assert rebuild_recorder["count"] == 0


async def test_rebuild_failure_rolls_back(api_client, pool, rebuild_recorder):
    """重建失败：500、DB 未写入"""
    rebuild_recorder["fail"] = True
    resp = await api_client.put("/api/settings", json={"weak": {"model": "w-bad"}})
    assert resp.status_code == 500
    assert await get_raw_settings(pool) == {}

async def test_tracking_preferences_roundtrip(api_client):
    """跟踪相关 preferences 键可写入并读回（含群列表数组）"""
    prefs = {
        "tracking_enabled": True,
        "tracking_interval_min": 30,
        "tracking_groups": ["兼职1群", "兼职2群"],
        "tracking_push_enabled": True,
        "tracking_push_limit": 5,
        "tracking_ledger_days": 7,
        "commute_origin": "静安寺",
    }
    resp = await api_client.put("/api/settings", json={"preferences": prefs})
    assert resp.status_code == 200
    assert resp.json()["agent_rebuilt"] is False

    data = (await api_client.get("/api/settings")).json()["preferences"]
    assert data["tracking_enabled"] is True
    assert data["tracking_interval_min"] == 30
    assert data["tracking_groups"] == ["兼职1群", "兼职2群"]
    assert data["commute_origin"] == "静安寺"
