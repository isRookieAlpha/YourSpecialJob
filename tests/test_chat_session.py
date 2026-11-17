# -*- coding: utf-8 -*-
"""chat/stream 自动建会话测试（假 agent，不调 LLM）。"""
import pytest

from agent.db.sessions_dao import list_sessions


class FakeAgent:
    """只产生一个空 updates 事件的最小异步生成器"""

    async def astream(self, inputs, config, stream_mode):
        yield "updates", {"model": {"messages": []}}


@pytest.fixture
def fake_agent(api_app):
    api_app.state.agent = FakeAgent()
    return api_app.state.agent


async def post_chat(client, message: str, thread_id: str | None = None) -> str:
    """发起流式聊天并读取完整响应（返回 SSE 文本）"""
    payload = {"thread_id": thread_id, "message": message}
    async with client.stream("POST", "/api/chat/stream", json=payload) as resp:
        assert resp.status_code == 200
        return await resp.aread()


async def test_chat_creates_session_with_title(api_client, pool, fake_agent):
    """首次聊天自动建会话，标题为消息前 20 字"""
    await post_chat(api_client, "读取兼职1群26/11/3的消息并推荐")
    sessions = await list_sessions(pool)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "读取兼职1群26/11/3的消息并推荐"[:20]


async def test_second_chat_keeps_title(api_client, pool, fake_agent):
    """后续消息不覆盖标题"""
    await post_chat(api_client, "第一条消息")
    thread_id = (await list_sessions(pool))[0]["id"]
    await post_chat(api_client, "第二条完全不同的消息", thread_id=thread_id)
    sessions = await list_sessions(pool)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "第一条消息"


async def test_existing_thread_not_duplicated(api_client, pool, fake_agent):
    """显式传入已有 thread_id 不重复插入"""
    await post_chat(api_client, "a", thread_id="fixed-thread")
    await post_chat(api_client, "b", thread_id="fixed-thread")
    assert len(await list_sessions(pool)) == 1