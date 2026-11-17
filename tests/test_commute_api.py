# -*- coding: utf-8 -*-
"""通勤重算接口测试。"""
import asyncio
from types import SimpleNamespace

import backend.routers.commute as commute_router
from agent.commute_refresh import CommuteRefreshState


async def test_status_structure(api_client):
    api_client._transport.app.state.commute_refresh_state = CommuteRefreshState()
    resp = await api_client.get("/api/commute/refresh/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("running", "pending", "total", "processed", "updated", "failed", "batches"):
        assert key in data


async def test_refresh_requires_origin(api_client, monkeypatch):
    api_client._transport.app.state.commute_refresh_state = CommuteRefreshState()
    api_client._transport.app.state.commute_refresh_wakeup = asyncio.Event()
    monkeypatch.setattr(
        commute_router, "get_settings", lambda: SimpleNamespace(preferences={})
    )
    resp = await api_client.post("/api/commute/refresh")
    assert resp.status_code == 400


async def test_refresh_wakes_worker(api_client, monkeypatch):
    state = CommuteRefreshState()
    wakeup = asyncio.Event()
    api_client._transport.app.state.commute_refresh_state = state
    api_client._transport.app.state.commute_refresh_wakeup = wakeup
    monkeypatch.setattr(
        commute_router, "get_settings",
        lambda: SimpleNamespace(preferences={"commute_origin": "静安寺"}),
    )
    resp = await api_client.post("/api/commute/refresh")
    assert resp.status_code == 202
    assert resp.json()["queued"] is False
    assert wakeup.is_set()


async def test_refresh_queued_while_running(api_client, monkeypatch):
    state = CommuteRefreshState()
    state.running = True
    wakeup = asyncio.Event()
    api_client._transport.app.state.commute_refresh_state = state
    api_client._transport.app.state.commute_refresh_wakeup = wakeup
    monkeypatch.setattr(
        commute_router, "get_settings",
        lambda: SimpleNamespace(preferences={"commute_origin": "静安寺"}),
    )
    resp = await api_client.post("/api/commute/refresh")
    assert resp.status_code == 202
    assert resp.json()["queued"] is True
    assert state.pending is True
    assert not wakeup.is_set()