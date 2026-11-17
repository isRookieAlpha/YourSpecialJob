# -*- coding: utf-8 -*-
"""POST /api/chat/resume 测试（FakeAgent 模拟中断状态与恢复流）。"""
from langgraph.types import Command, Interrupt

import backend.routers.chat as chat_mod


def make_interrupt(count: int = 1) -> Interrupt:
    return Interrupt(
        value={
            "action_requests": [
                {"name": "export_job_report", "args": {"title": f"报告{i}"}, "description": "审批"}
                for i in range(count)
            ],
            "review_configs": [
                {"action_name": "export_job_report",
                 "allowed_decisions": ["approve", "edit", "reject"]}
            ],
        },
        id="irq1",
    )


class FakeAgent:
    """模拟 aget_state（中断状态）与 astream（恢复后的流）"""

    def __init__(self, interrupts=(), events=None):
        self.interrupts = interrupts
        self.events = events or []
        self.received_inputs = []

    async def aget_state(self, config):
        interrupts = self.interrupts

        class Snapshot:
            values = {"messages": []}
            next = ()
        snap = Snapshot()
        snap.interrupts = interrupts
        return snap

    async def astream(self, inputs, config, stream_mode):
        self.received_inputs.append(inputs)
        for mode, payload in self.events:
            yield mode, payload


SSE_HEADERS = {"Accept": "text/event-stream"}


async def test_resume_success(api_client):
    """中断存在 + 决策数量匹配 → 200，Command(resume=...) 传给 agent"""
    agent = FakeAgent(interrupts=(make_interrupt(1),))
    api_client._transport.app.state.agent = agent

    resp = await api_client.post(
        "/api/chat/resume",
        json={"thread_id": "t1", "decisions": [{"type": "approve"}]},
        headers=SSE_HEADERS,
    )
    assert resp.status_code == 200
    assert "event: meta" in resp.text or "meta" in resp.text
    # 传给 agent 的是 Command 且 resume decisions 正确
    assert len(agent.received_inputs) == 1
    assert isinstance(agent.received_inputs[0], Command)
    assert agent.received_inputs[0].resume == {"decisions": [{"type": "approve"}]}


async def test_resume_no_interrupt(api_client):
    """thread 无中断 → 400"""
    api_client._transport.app.state.agent = FakeAgent(interrupts=())
    resp = await api_client.post(
        "/api/chat/resume",
        json={"thread_id": "t1", "decisions": [{"type": "approve"}]},
        headers=SSE_HEADERS,
    )
    assert resp.status_code == 400
    assert "没有待确认" in resp.json()["detail"]


async def test_resume_empty_decisions(api_client):
    """decisions 为空 → 400"""
    api_client._transport.app.state.agent = FakeAgent(interrupts=(make_interrupt(1),))
    resp = await api_client.post(
        "/api/chat/resume", json={"thread_id": "t1", "decisions": []}, headers=SSE_HEADERS
    )
    assert resp.status_code == 400


async def test_resume_count_mismatch(api_client):
    """decisions 数量与挂起工具调用不匹配 → 400"""
    api_client._transport.app.state.agent = FakeAgent(interrupts=(make_interrupt(2),))
    resp = await api_client.post(
        "/api/chat/resume",
        json={"thread_id": "t1", "decisions": [{"type": "approve"}]},
        headers=SSE_HEADERS,
    )
    assert resp.status_code == 400
    assert "数量不匹配" in resp.json()["detail"]


async def test_resume_agent_unavailable(api_client):
    """agent 为 None → 503"""
    api_client._transport.app.state.agent = None
    resp = await api_client.post(
        "/api/chat/resume",
        json={"thread_id": "t1", "decisions": [{"type": "approve"}]},
        headers=SSE_HEADERS,
    )
    assert resp.status_code == 503