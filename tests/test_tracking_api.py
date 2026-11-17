# -*- coding: utf-8 -*-
"""跟踪状态 API 测试。"""
import asyncio

from agent.tracking import TrackingState


async def test_status_structure(api_client):
    state = TrackingState()
    api_client._transport.app.state.tracking_state = state
    api_client._transport.app.state.tracking_wakeup = asyncio.Event()

    resp = await api_client.get("/api/tracking/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "enabled", "running", "interval_min", "push_enabled",
        "push_target_configured", "amap_configured", "paused_reason",
        "progress", "worker_pid", "last_round", "groups", "push_24h",
    ):
        assert key in data
    # 不泄露密钥/完整 sender
    text = resp.text
    assert "sk-" not in text.lower()


async def test_run_wakes_task(api_client):
    wakeup = asyncio.Event()
    api_client._transport.app.state.tracking_wakeup = wakeup
    resp = await api_client.post("/api/tracking/run")
    assert resp.status_code == 202
    assert wakeup.is_set()


async def test_run_403_when_wechat_access_disabled(api_client, monkeypatch):
    import backend.routers.tracking as tracking_router

    async def fake_load_config(pool):
        from types import SimpleNamespace
        return SimpleNamespace(wechat_access_enabled=False)

    monkeypatch.setattr(tracking_router, "load_config", fake_load_config)
    resp = await api_client.post("/api/tracking/run")
    assert resp.status_code == 403
    assert "微信访问" in resp.json()["detail"]


async def test_run_without_task_503(api_client):
    if hasattr(api_client._transport.app.state, "tracking_wakeup"):
        api_client._transport.app.state.tracking_wakeup = None
    resp = await api_client.post("/api/tracking/run")
    assert resp.status_code == 503


async def test_health_contains_tracking(api_client):
    api_client._transport.app.state.tracking_state = TrackingState()
    resp = await api_client.get("/api/health")
    data = resp.json()
    assert "tracking" in data
    assert data["tracking"]["running"] is False