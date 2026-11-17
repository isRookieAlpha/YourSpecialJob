# -*- coding: utf-8 -*-
"""SSE 事件映射测试（假 agent 模拟 astream 事件形状，不调 LLM）。"""
import json

from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Interrupt

from backend.sse import sse_events


class FakeAgent:
    """模拟 agent.astream 的事件序列（形状与 probe_astream 实测一致）"""

    def __init__(self, events, raise_at_end: bool = False):
        self.events = events
        self.raise_at_end = raise_at_end

    async def astream(self, inputs, config, stream_mode):
        for mode, payload in self.events:
            yield mode, payload
        if self.raise_at_end:
            raise RuntimeError("模拟流中断")


def parse(events: list[dict]) -> list[tuple[str, dict]]:
    return [(e["event"], json.loads(e["data"])) for e in events]


async def collect(agent) -> list[tuple[str, dict]]:
    return parse([e async for e in sse_events(agent, "t1", {"messages": [{"role": "user", "content": "你好"}]})])


async def test_token_todos_tool_done_sequence():
    """正常流：meta → token（过滤非 model 节点）→ todos 全量快照 → tool → done"""
    agent = FakeAgent([
        ("messages", (AIMessageChunk(content="你"), {"langgraph_node": "model"})),
        ("messages", (AIMessageChunk(content=""), {"langgraph_node": "model"})),  # 空增量过滤
        ("messages", (AIMessageChunk(content="摘要泄漏"), {"langgraph_node": "SummarizationMiddleware.before_model"})),
        ("updates", {"tools": {
            "todos": [{"content": "查库", "status": "in_progress"}],
            "messages": [ToolMessage(content="命中 3 条", name="query_jobs", tool_call_id="c1")],
        }}),
        ("updates", {"model": {"messages": []}}),  # 无 todos 的节点忽略
    ])
    events = await collect(agent)

    assert events[0] == ("meta", {"thread_id": "t1"})
    assert events[1] == ("token", {"content": "你"})
    assert events[2][0] == "todos"
    assert events[2][1]["items"] == [{"content": "查库", "status": "in_progress"}]
    assert events[3][0] == "tool"
    assert events[3][1]["name"] == "query_jobs"
    assert "命中 3 条" in events[3][1]["preview"]
    assert events[-1] == ("done", {"thread_id": "t1"})
    # 摘要节点的 token 与空增量都没有泄漏
    assert [e for e in events if e[0] == "token"] == [("token", {"content": "你"})]


async def test_exception_yields_error_event():
    """流中异常 → error 事件（中文），不抛出"""
    agent = FakeAgent([], raise_at_end=True)
    events = await collect(agent)
    assert events[0][0] == "meta"
    assert events[-1][0] == "error"
    assert "处理失败" in events[-1][1]["message"]


async def test_offers_custom_event_passthrough():
    """custom 通道 offers 事件 → SSE offers（心动的offer 侧栏数据源）"""
    agent = FakeAgent([
        ("custom", {"type": "offers", "items": [{"id": 1, "work_summary": "传菜"}]}),
        ("custom", {"type": "unknown_type", "x": 1}),  # 未知 custom 忽略
        ("messages", (AIMessageChunk(content="好的"), {"langgraph_node": "model"})),
    ])
    events = await collect(agent)
    offers = [e for e in events if e[0] == "offers"]
    assert len(offers) == 1
    assert offers[0][1]["items"] == [{"id": 1, "work_summary": "传菜"}]
    assert events[-1][0] == "done"


async def test_empty_stream_yields_quota_error_hint():
    """模型静默失败（无 token/工具/HITL）→ error 事件提示额度/配置（不再空气泡）"""
    agent = FakeAgent([])
    events = await collect(agent)
    assert events[0][0] == "meta"
    errors = [e for e in events if e[0] == "error"]
    assert len(errors) == 1
    assert "额度" in errors[0][1]["message"]
    assert events[-1][0] == "done"


async def test_interrupt_yields_hitl_event():
    """__interrupt__（probe_hitl 实测形状）→ hitl 事件 + 正常 done"""
    interrupt = Interrupt(
        value={
            "action_requests": [
                {"name": "export_job_report", "args": {"title": "11月兼职"},
                 "description": "工具执行需要审批\n\nTool: export_job_report"}
            ],
            "review_configs": [
                {"action_name": "export_job_report",
                 "allowed_decisions": ["approve", "edit", "reject"]}
            ],
        },
        id="abc123",
    )
    agent = FakeAgent([
        ("updates", {"model": {"messages": []}}),
        ("updates", {"__interrupt__": (interrupt,)}),
    ])
    events = await collect(agent)

    hitl = [e for e in events if e[0] == "hitl"]
    assert len(hitl) == 1
    data = hitl[0][1]
    assert data["thread_id"] == "t1"
    assert data["actions"][0]["name"] == "export_job_report"
    assert data["actions"][0]["args"] == {"title": "11月兼职"}
    assert data["allowed"] == [["approve", "edit", "reject"]]
    # 中断后流自然结束，正常发 done
    assert events[-1] == ("done", {"thread_id": "t1"})

async def test_on_complete_triggered_on_normal_end():
    """正常结束（有 token、无 HITL）→ 后台记忆提取回调被触发"""
    import asyncio

    calls = []

    async def hook(user_message):
        calls.append(user_message)

    agent = FakeAgent([
        ("messages", (AIMessageChunk(content="好的"), {"langgraph_node": "model"})),
    ])
    events = [
        e async for e in sse_events(
            agent, "t1", {"messages": [{"role": "user", "content": "找兼职"}]},
            user_message="找兼职", on_complete=hook,
        )
    ]
    await asyncio.sleep(0)  # 让 create_task 的 hook 执行
    assert calls == ["找兼职"]
    assert events[-1]["event"] == "done"


async def test_on_complete_not_triggered_on_hitl():
    """HITL 中断时不做记忆提取"""
    import asyncio

    calls = []

    async def hook(user_message):
        calls.append(user_message)

    agent = FakeAgent([
        ("messages", (AIMessageChunk(content="需要确认"), {"langgraph_node": "model"})),
        ("updates", {"__interrupt__": (Interrupt(value={"action_requests": [], "review_configs": []}, id="x"),)}),
    ])
    [e async for e in sse_events(agent, "t1", {"messages": []}, user_message="x", on_complete=hook)]
    await asyncio.sleep(0)
    assert calls == []


async def test_on_complete_not_triggered_on_error():
    """流异常时不做记忆提取"""
    import asyncio

    calls = []

    async def hook(user_message):
        calls.append(user_message)

    agent = FakeAgent([], raise_at_end=True)
    [e async for e in sse_events(agent, "t1", {"messages": []}, user_message="x", on_complete=hook)]
    await asyncio.sleep(0)
    assert calls == []
