# -*- coding: utf-8 -*-
"""工具调用参数修复中间件测试（不调用真实模型）。"""
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from agent.tool_call_repair import (
    ToolCallRepairMiddleware,
    repair_invalid_tool_calls,
    repair_json_arguments,
)


def invalid_call(args: str, name: str = "ingest_wechat_group_messages", call_id: str = "call_1"):
    return {"type": "invalid_tool_call", "id": call_id, "name": name, "args": args, "error": None}


class TestRepairArguments:
    def test_bare_chinese_value_gets_quotes(self):
        assert repair_json_arguments('{"group_name": 兼职1群}') == {"group_name": "兼职1群"}

    def test_numbers_bool_null_keep_literal(self):
        assert repair_json_arguments('{"limit": 5, "enabled": true, "note": null}') == {
            "limit": 5, "enabled": True, "note": None,
        }

    def test_valid_json_untouched(self):
        assert repair_json_arguments('{"group_name": "兼职1群"}') == {"group_name": "兼职1群"}

    def test_empty_arguments(self):
        assert repair_json_arguments("") == {}

    def test_unrepairable_returns_none(self):
        assert repair_json_arguments('{"todos": [{"content": "x"') is None

    def test_non_dict_returns_none(self):
        assert repair_json_arguments('["a", "b"]') is None


class TestRepairMessage:
    def test_invalid_call_moved_to_tool_calls(self):
        msg = AIMessage(
            content="好的，我来读取",
            invalid_tool_calls=[invalid_call('{"group_name": 兼职1群}')],
        )
        fixed = repair_invalid_tool_calls(msg)
        assert len(fixed.tool_calls) == 1
        assert fixed.tool_calls[0]["name"] == "ingest_wechat_group_messages"
        assert fixed.tool_calls[0]["args"] == {"group_name": "兼职1群"}
        assert fixed.tool_calls[0]["id"] == "call_1"
        assert fixed.invalid_tool_calls == []
        assert fixed.content == "好的，我来读取"

    def test_unrepairable_stays_invalid(self):
        bad = invalid_call('{"todos": [{"content": "x"')
        msg = AIMessage(content="", invalid_tool_calls=[bad])
        fixed = repair_invalid_tool_calls(msg)
        assert fixed.tool_calls == []
        assert len(fixed.invalid_tool_calls) == 1

    def test_mixed_calls_kept_and_repaired(self):
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "query_jobs", "args": {}, "id": "ok_1", "type": "tool_call"}],
            invalid_tool_calls=[invalid_call('{"contact_name": 小王}')],
        )
        fixed = repair_invalid_tool_calls(msg)
        assert [c["id"] for c in fixed.tool_calls] == ["ok_1", "call_1"]

    def test_message_without_invalid_calls_unchanged(self):
        msg = AIMessage(content="普通回复")
        assert repair_invalid_tool_calls(msg) is msg


class TestMiddleware:
    async def test_async_wrap_repairs_response(self):
        response = ModelResponse(result=[
            AIMessage(content="……", invalid_tool_calls=[invalid_call('{"group_name": 兼职1群}')]),
        ])

        async def handler(request):
            return response

        repaired = await ToolCallRepairMiddleware().awrap_model_call(None, handler)
        message = repaired.result[0]
        assert message.tool_calls[0]["args"] == {"group_name": "兼职1群"}

    def test_sync_wrap_repairs_response(self):
        response = ModelResponse(result=[
            AIMessage(content="", invalid_tool_calls=[invalid_call('{"group_name": 兼职1群}')]),
        ])
        repaired = ToolCallRepairMiddleware().wrap_model_call(None, lambda request: response)
        assert repaired.result[0].tool_calls
