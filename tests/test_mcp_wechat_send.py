# -*- coding: utf-8 -*-
"""mcp_wechat 发送/读取封装测试（mock MCP 工具调用，不真实操作微信）。"""
import asyncio

import pytest

import agent.mcp_wechat as mcp_mod


@pytest.fixture
def fake_tool(monkeypatch):
    """替换底层工具调用，返回预置文本"""
    answers = {}

    async def fake_invoke(tool_name, arguments, timeout):
        answers["tool"] = tool_name
        answers["args"] = arguments
        answer = answers.get("answer", "")
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fake_run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(mcp_mod, "_invoke_tool_async", fake_invoke)
    monkeypatch.setattr(mcp_mod, "_run_in_proactor_loop", fake_run)
    return answers


class TestSendWechatMessage:
    async def test_success(self, fake_tool):
        fake_tool["answer"] = '{"status": "success", "message": "消息已发送给 文件传输助手"}'
        result = await mcp_mod.send_wechat_message("文件传输助手", "你好")
        assert result["status"] == "success"
        assert fake_tool["tool"] == "wechat_send_message"
        assert fake_tool["args"] == {"to_user": "文件传输助手", "message": "你好"}

    async def test_error_status_raises(self, fake_tool):
        fake_tool["answer"] = '{"status": "error", "message": "发送消息失败: 未登录"}'
        with pytest.raises(mcp_mod.WechatMcpError, match="微信发送失败"):
            await mcp_mod.send_wechat_message("文件传输助手", "你好")

    async def test_non_json_raises(self, fake_tool):
        fake_tool["answer"] = "not a json"
        with pytest.raises(mcp_mod.WechatMcpError, match="格式异常"):
            await mcp_mod.send_wechat_message("文件传输助手", "你好")


class TestAllowEmpty:
    EMPTY = "获取到 0 条与 X 在 26/11/5 的聊天记录\n\n"

    async def test_read_empty_allowed(self, fake_tool):
        fake_tool["answer"] = self.EMPTY
        messages = await mcp_mod.call_wechat_get_history("兼职1群", "26/11/5", allow_empty=True)
        assert messages == []

    async def test_read_empty_default_raises(self, fake_tool):
        fake_tool["answer"] = self.EMPTY
        with pytest.raises(mcp_mod.WechatMcpError, match="为空"):
            await mcp_mod.call_wechat_get_history("兼职1群", "26/11/5")

class TestCombinePublishTime:
    def test_returns_local_aware(self):
        """消息时刻按本地时区合成（避免 naive 被当 UTC 导致 +8 小时）"""
        from datetime import date

        pub = mcp_mod.combine_publish_time("11:38", date(2026, 11, 6))
        assert pub is not None
        assert pub.utcoffset() is not None
        assert (pub.hour, pub.minute) == (11, 38)

    def test_invalid_returns_none(self):
        from datetime import date

        assert mcp_mod.combine_publish_time(None, date(2026, 11, 6)) is None
        assert mcp_mod.combine_publish_time("不是时间", date(2026, 11, 6)) is None
