# -*- coding: utf-8 -*-
"""历史消息转换测试（convert_history_messages 纯函数 + API 端点）。"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.history import convert_history_messages, convert_history_todos


class TestConvertHistoryMessages:
    """convert_history_messages 分支测试"""

    def test_human_message_to_user(self):
        """HumanMessage → user"""
        msgs = [HumanMessage(content="你好")]
        result = convert_history_messages(msgs)
        assert result == [{"role": "user", "content": "你好"}]

    def test_ai_message_to_assistant(self):
        """AIMessage 非空 content → assistant"""
        msgs = [AIMessage(content="你好！有什么可以帮你的？")]
        result = convert_history_messages(msgs)
        assert result == [{"role": "assistant", "content": "你好！有什么可以帮你的？"}]

    def test_ai_message_empty_with_tool_calls_skipped(self):
        """AIMessage 空 content + tool_calls → 跳过（工具调用请求不显示）"""
        msgs = [AIMessage(content="", tool_calls=[{"name": "query_jobs", "args": {}, "id": "c1"}])]
        result = convert_history_messages(msgs)
        assert result == []

    def test_tool_message_to_tool(self):
        """ToolMessage → tool（preview[:300]）"""
        long_content = "x" * 500
        msgs = [ToolMessage(content=long_content, name="query_jobs", tool_call_id="c1")]
        result = convert_history_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["name"] == "query_jobs"
        assert len(result[0]["content"]) == 300  # 截断到 300

    def test_multimodal_content_list(self):
        """content 为 list（多模态块）时拼文本"""
        msgs = [HumanMessage(content=[
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ])]
        result = convert_history_messages(msgs)
        assert result == [{"role": "user", "content": "看这张图"}]

    def test_empty_messages_skipped(self):
        """空 content 消息跳过"""
        msgs = [HumanMessage(content=""), AIMessage(content="   ")]
        result = convert_history_messages(msgs)
        assert result == []

    def test_full_conversation_sequence(self):
        """完整对话序列（probe_hitl 实测形状）"""
        msgs = [
            HumanMessage(content="帮我导出报告"),
            AIMessage(content="", tool_calls=[{"name": "dummy_export", "args": {"title": "测试"}, "id": "c1"}]),
            ToolMessage(content="已导出报告：测试", name="dummy_export", tool_call_id="c1"),
            AIMessage(content="已成功导出报告。"),
        ]
        result = convert_history_messages(msgs)
        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "帮我导出报告"}
        assert result[1] == {"role": "tool", "name": "dummy_export", "content": "已导出报告：测试"}
        assert result[2] == {"role": "assistant", "content": "已成功导出报告。"}


class TestConvertHistoryTodos:
    """convert_history_todos 测试"""

    def test_todos_present(self):
        values = {"todos": [{"content": "查库", "status": "completed"}, {"content": "推荐", "status": "in_progress"}]}
        result = convert_history_todos(values)
        assert result == [
            {"content": "查库", "status": "completed"},
            {"content": "推荐", "status": "in_progress"},
        ]

    def test_todos_absent(self):
        assert convert_history_todos({}) == []
        assert convert_history_todos({"todos": None}) == []
        assert convert_history_todos({"todos": []}) == []


class TestHistoryAPI:
    """GET /api/sessions/{id}/messages API 测试"""

    async def test_get_messages_success(self, api_client):
        """正常获取历史消息"""
        msgs = [HumanMessage(content="你好"), AIMessage(content="你好！")]

        class FakeAgent:
            async def aget_state(self, config):
                class FakeSnapshot:
                    values = {"messages": msgs, "todos": []}
                    next = ()
                    interrupts = ()
                return FakeSnapshot()

        api_client._transport.app.state.agent = FakeAgent()

        resp = await api_client.post("/api/sessions", json={"title": "测试会话"})
        session_id = resp.json()["id"]

        resp = await api_client.get(f"/api/sessions/{session_id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    async def test_get_messages_empty_thread(self, api_client):
        """thread 无 checkpoint → 空数组"""

        class FakeAgent:
            async def aget_state(self, config):
                class FakeSnapshot:
                    values = {"messages": [], "todos": []}
                    next = ()
                    interrupts = ()
                return FakeSnapshot()

        api_client._transport.app.state.agent = FakeAgent()

        resp = await api_client.post("/api/sessions", json={"title": "空会话"})
        session_id = resp.json()["id"]

        resp = await api_client.get(f"/api/sessions/{session_id}/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    async def test_get_messages_session_not_found(self, api_client):
        """会话不存在 → 404"""
        resp = await api_client.get("/api/sessions/nonexistent/messages")
        assert resp.status_code == 404

    async def test_get_messages_agent_unavailable(self, api_client):
        """agent 为 None → 503"""
        api_client._transport.app.state.agent = None

        resp = await api_client.post("/api/sessions", json={"title": "测试"})
        session_id = resp.json()["id"]

        resp = await api_client.get(f"/api/sessions/{session_id}/messages")
        assert resp.status_code == 503