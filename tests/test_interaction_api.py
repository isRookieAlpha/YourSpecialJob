# -*- coding: utf-8 -*-
"""中介互动 API 测试：状态 / 待确认列表 / 确认 / 立即执行（ASGITransport，不跑 lifespan）。"""
import asyncio

from langchain_core.messages import AIMessage

import agent.contact_interaction as contact_mod
from agent.db.interaction_dao import (
    CONTACT_THREAD_PREFIX,
    get_contact_pending,
    save_pending,
)

CONTACT = "小王"
THREAD = f"{CONTACT_THREAD_PREFIX}{CONTACT}"


class FakeAgent:
    def __init__(self, reply: str = "已处理"):
        self.reply = reply
        self.calls: list[tuple] = []

    async def ainvoke(self, inputs, config):
        self.calls.append((inputs, config))
        return {"messages": [AIMessage(content=self.reply)]}


class TestContactStatus:
    async def test_default_status(self, api_client):
        resp = await api_client.get("/api/interaction/contact-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["contacts"] == []
        assert data["pending_count"] == 0
        assert data["enabled"] is False

    async def test_status_lists_pending(self, api_client, pool):
        await save_pending(pool, THREAD, CONTACT, {
            "mode": "draft", "draft": "您好", "source_message": "在吗",
        })
        resp = await api_client.get("/api/interaction/contact-status")
        data = resp.json()
        assert data["pending_count"] == 1
        assert data["pending"][0]["contact_name"] == CONTACT


class TestContactPendingList:
    async def test_empty(self, api_client):
        resp = await api_client.get("/api/interaction/contact-pending")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_public_fields_only(self, api_client, pool):
        await save_pending(pool, THREAD, CONTACT, {
            "mode": "hitl",
            "draft": "您好",
            "source_message": "在吗",
            "action_requests": [{"name": "send_wechat_message_to_contact", "args": {}}],
        })
        resp = await api_client.get("/api/interaction/contact-pending")
        item = resp.json()["items"][0]
        assert item["thread_id"] == THREAD
        assert item["contact_name"] == CONTACT
        assert item["draft"] == "您好"
        assert item["source_message"] == "在吗"
        assert "action_requests" not in item


class TestContactPendingResume:
    async def test_bad_prefix(self, api_client):
        resp = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": "wechat:Alpha", "decision": "approve",
        })
        assert resp.status_code == 409

    async def test_bad_decision(self, api_client):
        resp = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "maybe",
        })
        assert resp.status_code == 400

    async def test_agent_missing(self, api_client, pool):
        await save_pending(pool, THREAD, CONTACT, {"mode": "draft", "draft": "hi"})
        resp = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "approve",
        })
        assert resp.status_code == 503

    async def test_not_found(self, api_client, api_app):
        api_app.state.agent = FakeAgent()
        resp = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "approve",
        })
        assert resp.status_code == 404

    async def test_draft_approve_sends(self, api_client, api_app, pool, monkeypatch):
        calls = []

        async def fake_deliver(pool_arg, contact, message):
            calls.append((contact, message))
            return f"已发送给「{contact}」。"

        monkeypatch.setattr(contact_mod, "deliver_to_contact", fake_deliver)
        api_app.state.agent = object()
        await save_pending(pool, THREAD, CONTACT, {"mode": "draft", "draft": "您好"})

        resp = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "approve",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert calls == [(CONTACT, "您好")]
        assert await get_contact_pending(pool) is None

    async def test_second_channel_gets_404(self, api_client, api_app, pool, monkeypatch):
        async def fake_deliver(pool_arg, contact, message):
            return "sent"

        monkeypatch.setattr(contact_mod, "deliver_to_contact", fake_deliver)
        api_app.state.agent = object()
        await save_pending(pool, THREAD, CONTACT, {"mode": "draft", "draft": "您好"})

        first = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "approve",
        })
        second = await api_client.post("/api/interaction/contact-pending/resume", json={
            "thread_id": THREAD, "decision": "reject",
        })
        assert first.status_code == 200
        assert second.status_code == 404


class TestContactRun:
    async def test_wakeup_missing(self, api_client, api_app):
        api_app.state.contact_interaction_wakeup = None
        resp = await api_client.post("/api/interaction/contact-run")
        assert resp.status_code == 503

    async def test_wakeup_set(self, api_client, api_app):
        event = asyncio.Event()
        api_app.state.contact_interaction_wakeup = event
        resp = await api_client.post("/api/interaction/contact-run")
        assert resp.status_code == 202
        assert event.is_set()