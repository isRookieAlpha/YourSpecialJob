# -*- coding: utf-8 -*-
"""微信互动 worker 测试（mock 微信读取/发送/agent，真实测试库账本与 pending）。"""
import asyncio
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command, Interrupt

import agent.interaction as interaction_mod
from agent.interaction import (
    INTERACTION_REPLY_MAX_LEN,
    InteractionState,
    _parse_decision,
    build_interrupt_notice,
    poll_interaction,
)
from agent.preferences import InteractionConfig

CONV = "Alpha"
THREAD = f"wechat:{CONV}"


def cfg(**kwargs) -> InteractionConfig:
    base = dict(enabled=True, interval_sec=60, target=CONV,
                max_replies_per_hour=30, pending_ttl_min=1440)
    base.update(kwargs)
    return InteractionConfig(**base)


def msg(i: int, text: str, t: str = "10:00", sender: str = "Alpha") -> dict:
    return {"index": i, "发送者": sender, "时间": t, "消息": text}


class FakeAgent:
    def __init__(self, reply: str = "好的，已处理", interrupt: dict | None = None):
        self.reply = reply
        self.interrupt = interrupt
        self.calls: list[tuple] = []

    async def ainvoke(self, inputs, config):
        self.calls.append((inputs, config))
        result = {"messages": [AIMessage(content=self.reply)]}
        if self.interrupt is not None:
            result["__interrupt__"] = (Interrupt(value=self.interrupt, id="irq-1"),)
        return result


@pytest.fixture
def patch_io(monkeypatch):
    state = {"messages": [], "sent": []}

    async def fake_read(conversation, label, allow_empty=False, timeout=180):
        return list(state["messages"])

    async def fake_send(conversation, text):
        state["sent"].append(text)
        return {"status": "success"}

    monkeypatch.setattr(interaction_mod, "call_wechat_get_history", fake_read)
    monkeypatch.setattr(interaction_mod, "send_wechat_message", fake_send)
    monkeypatch.setattr(interaction_mod, "SEND_COOLDOWN_SEC", 0)  # 测试不等待冷却
    return state


class TestDecisionParsing:
    def test_approve_words(self):
        assert _parse_decision("同意") == "approve"
        assert _parse_decision("同意执行吧") == "approve"
        assert _parse_decision("确认") == "approve"

    def test_reject_words(self):
        assert _parse_decision("拒绝") == "reject"
        assert _parse_decision("取消吧") == "reject"

    def test_other(self):
        assert _parse_decision("帮我找兼职") is None
        assert _parse_decision("") is None


class TestWithOfferList:
    def test_appends_when_missing(self):
        from langchain_core.messages import ToolMessage
        from agent.interaction import _with_offer_list

        result = {"messages": [ToolMessage(
            content="已记录本轮推荐（1 条）：\n1. 酒店传菜（id=1）\n微信端可回复「添加第 N 条」",
            name="select_offers",
            tool_call_id="call-1",
        )]}
        text = _with_offer_list("推荐已同步。", result)
        assert text.startswith("推荐已同步。")
        assert "1. 酒店传菜（id=1）" in text
        assert "添加第 N 条" in text

    def test_no_duplicate_when_present(self):
        from langchain_core.messages import ToolMessage
        from agent.interaction import _with_offer_list

        result = {"messages": [ToolMessage(
            content="已记录本轮推荐（1 条）：\n1. 酒店传菜（id=1）\n添加第 N 条",
            name="select_offers",
            tool_call_id="call-1",
        )]}
        assert _with_offer_list("1. 酒店传菜（id=1）", result) == "1. 酒店传菜（id=1）"

    def test_no_tool_message(self):
        from agent.interaction import _with_offer_list

        assert _with_offer_list("普通回复", {"messages": []}) == "普通回复"


class TestInterruptNotice:
    def test_contains_instructions(self):
        text = build_interrupt_notice({
            "action_requests": [{"name": "export_job_report", "args": {"title": "报告"}}],
        })
        assert "审批请求" in text
        assert "export_job_report" in text
        assert "同意" in text and "拒绝" in text


class TestPollInteraction:
    async def test_wechat_access_disabled_skips(self, pool, patch_io):
        """微信访问关闭 → 不读微信、不调用 agent"""
        patch_io["messages"] = [msg(0, "你好")]
        agent = FakeAgent()
        result = await poll_interaction(
            pool, agent, cfg(wechat_access_enabled=False), InteractionState()
        )
        assert result["skipped"] is True
        assert result["reason"] == "微信访问已关闭"
        assert agent.calls == []
        assert patch_io["sent"] == []

    async def test_baseline_records_without_reply(self, pool, patch_io, monkeypatch):
        patch_io["messages"] = [msg(0, "历史消息一"), msg(1, "历史消息二")]
        agent = FakeAgent()
        state = InteractionState()
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("baseline") is True
        assert patch_io["sent"] == []
        assert agent.calls == []

    async def test_new_message_replies(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent(reply="这是回答")
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)  # 基线

        patch_io["messages"].append(msg(1, "帮我找明天的兼职", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("replied") is True
        assert len(agent.calls) == 1
        assert patch_io["sent"] and "这是回答" in patch_io["sent"][-1]
        assert patch_io["sent"][-1].startswith("【YourSpecialJob 回复】")

    async def test_reply_triggers_memory_extract(self, pool, patch_io, monkeypatch):
        """正常回复成功后 fire-and-forget 提取长期记忆"""
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent(reply="这是回答")
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)  # 基线

        patch_io["messages"].append(msg(1, "我以后不接夜班", t="11:00"))
        calls: list[tuple] = []

        async def fake_extract(store, thread_id, user_message, reply):
            calls.append((store, thread_id, user_message, reply))
            return 1

        monkeypatch.setattr(interaction_mod, "extract_and_store_memory", fake_extract)
        marker = object()
        await poll_interaction(pool, agent, cfg(), state, store=marker)
        await asyncio.sleep(0)  # 等待 fire-and-forget 任务执行
        assert calls
        assert calls[0][0] is marker
        assert calls[0][1] == THREAD
        assert "不接夜班" in calls[0][2]
        assert "这是回答" in calls[0][3]

    async def test_robot_messages_ignored(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent()
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        patch_io["messages"].append(msg(1, "【YourSpecialJob 兼职跟踪】新兼职", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("new_messages") == 0
        assert agent.calls == [] and patch_io["sent"] == []

    async def test_duplicate_not_replied_twice(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent()
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        patch_io["messages"].append(msg(1, "找兼职", t="11:00"))
        await poll_interaction(pool, agent, cfg(), state)
        result = await poll_interaction(pool, agent, cfg(), state)  # 同一消息再轮询
        assert result.get("new_messages") == 0
        assert len(agent.calls) == 1

    async def test_interrupt_saves_pending_and_notifies(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        interrupt = {"action_requests": [{"name": "export_job_report", "args": {"title": "x"}}],
                     "review_configs": []}
        agent = FakeAgent(interrupt=interrupt)
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        patch_io["messages"].append(msg(1, "导出报告", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("replied") is True
        assert "审批请求" in patch_io["sent"][-1]

        from agent.db.interaction_dao import get_pending
        pending = await get_pending(pool, THREAD)
        assert pending is not None

    async def test_pending_approve_resumes(self, pool, patch_io):
        from agent.db.interaction_dao import get_pending, save_pending

        await save_pending(pool, THREAD, CONV, {"action_requests": [{"name": "export_job_report"}]})
        patch_io["messages"] = [msg(0, "历史消息")]  # 基线先建立
        agent = FakeAgent(reply="报告已生成")
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)  # 基线（有 pending 但 baseline 优先）

        patch_io["messages"].append(msg(1, "同意", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("pending") is True
        assert agent.calls, "应调用 ainvoke 恢复"
        inputs, _ = agent.calls[-1]
        assert isinstance(inputs, Command)
        assert inputs.resume == {"decisions": [{"type": "approve"}]}
        assert await get_pending(pool, THREAD) is None
        assert "报告已生成" in patch_io["sent"][-1]

    async def test_pending_multi_actions_resume_all(self, pool, patch_io):
        """多工具同时中断：decisions 数量与 action_requests 一致（否则中间件报错）"""
        from agent.db.interaction_dao import save_pending

        await save_pending(pool, THREAD, CONV, {"action_requests": [
            {"name": "export_job_report", "args": {}},
            {"name": "send_wechat_message_to_contact",
             "args": {"contact_name": "中介", "message": "hi"}},
        ]})
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent(reply="已处理")
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)  # 基线

        patch_io["messages"].append(msg(1, "同意", t="11:00"))
        await poll_interaction(pool, agent, cfg(), state)
        inputs, _ = agent.calls[-1]
        assert isinstance(inputs, Command)
        assert len(inputs.resume["decisions"]) == 2
        assert all(d["type"] == "approve" for d in inputs.resume["decisions"])

    async def test_pending_reject_resumes(self, pool, patch_io):
        from agent.db.interaction_dao import save_pending

        await save_pending(pool, THREAD, CONV, {"action_requests": [{"name": "export_job_report"}]})
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent(reply="已取消")
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        patch_io["messages"].append(msg(1, "拒绝", t="11:00"))
        await poll_interaction(pool, agent, cfg(), state)
        inputs, _ = agent.calls[-1]
        assert inputs.resume["decisions"][0]["type"] == "reject"

    async def test_pending_other_message_prompts(self, pool, patch_io):
        from agent.db.interaction_dao import get_pending, save_pending

        await save_pending(pool, THREAD, CONV, {"action_requests": [{"name": "x"}]})
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent()
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        patch_io["messages"].append(msg(1, "帮我找兼职", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("pending") is True
        assert "同意" in patch_io["sent"][-1]
        assert await get_pending(pool, THREAD) is not None  # pending 保留

    async def test_rate_limit(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        agent = FakeAgent()
        state = InteractionState()
        await poll_interaction(pool, agent, cfg(), state)

        # 打满每小时上限
        state.reply_times = [__import__("time").time()] * 5
        patch_io["messages"].append(msg(1, "找兼职", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(max_replies_per_hour=5), state)
        assert result.get("skipped") is True

    async def test_send_cooldown(self, pool, patch_io, monkeypatch):
        monkeypatch.setattr(interaction_mod, "SEND_COOLDOWN_SEC", 8)
        state = InteractionState()
        import time as _time
        state.last_reply_at = _time.time()
        agent = FakeAgent()
        result = await poll_interaction(pool, agent, cfg(), state)
        assert result.get("skipped") is True

    async def test_no_target(self, pool, patch_io):
        state = InteractionState()
        result = await poll_interaction(pool, FakeAgent(), cfg(target=""), state)
        assert result.get("skipped") is True

class TestContactPendingRouting:
    async def test_approve_routes_to_contact_pending(self, pool, patch_io, monkeypatch):
        """用户在互动会话回复「同意」→ 处理中介待确认（而非用户 thread）"""
        from agent.db.interaction_dao import (
            CONTACT_THREAD_PREFIX,
            get_contact_pending,
            save_pending,
        )

        patch_io["messages"] = [msg(0, "历史消息")]
        state = InteractionState()
        await poll_interaction(pool, FakeAgent(), cfg(), state)  # 基线

        thread = f"{CONTACT_THREAD_PREFIX}小王"
        await save_pending(pool, thread, "小王", {
            "mode": "draft", "draft": "您好", "contact_name": "小王",
        })
        calls = []

        async def fake_resolve(pool_arg, agent, pending, decision, pending_ttl_min=1440):
            calls.append((pending["conversation"], decision))
            return {"ok": True, "message": "已发送给「小王」。", "contact_name": "小王"}

        import agent.contact_interaction as contact_mod

        monkeypatch.setattr(contact_mod, "resolve_contact_pending", fake_resolve)

        patch_io["messages"].append(msg(1, "同意", t="11:00"))
        result = await poll_interaction(pool, FakeAgent(), cfg(), state)

        assert result.get("contact_pending") is True
        assert calls == [("小王", "approve")]
        assert "已发送给「小王」" in patch_io["sent"][-1]
        assert await get_contact_pending(pool) is None

    async def test_reject_routes_to_contact_pending(self, pool, patch_io, monkeypatch):
        from agent.db.interaction_dao import CONTACT_THREAD_PREFIX, save_pending

        patch_io["messages"] = [msg(0, "历史消息")]
        state = InteractionState()
        await poll_interaction(pool, FakeAgent(), cfg(), state)

        await save_pending(pool, f"{CONTACT_THREAD_PREFIX}小王", "小王", {"mode": "draft"})
        calls = []

        async def fake_resolve(pool_arg, agent, pending, decision, pending_ttl_min=1440):
            calls.append(decision)
            return {"ok": True, "message": "已取消。", "contact_name": "小王"}

        import agent.contact_interaction as contact_mod

        monkeypatch.setattr(contact_mod, "resolve_contact_pending", fake_resolve)

        patch_io["messages"].append(msg(1, "拒绝", t="11:00"))
        await poll_interaction(pool, FakeAgent(), cfg(), state)
        assert calls == ["reject"]

    async def test_non_decision_message_does_not_consume_pending(self, pool, patch_io):
        """普通聊天不拦截、不消耗中介待确认"""
        from agent.db.interaction_dao import (
            CONTACT_THREAD_PREFIX,
            get_contact_pending,
            save_pending,
        )

        patch_io["messages"] = [msg(0, "历史消息")]
        state = InteractionState()
        await poll_interaction(pool, FakeAgent(), cfg(), state)

        await save_pending(pool, f"{CONTACT_THREAD_PREFIX}小王", "小王", {"mode": "draft"})
        patch_io["messages"].append(msg(1, "帮我找兼职", t="11:00"))
        agent = FakeAgent(reply="这是回答")
        result = await poll_interaction(pool, agent, cfg(), state)

        assert result.get("replied") is True
        assert result.get("contact_pending") is None
        assert agent.calls
        assert await get_contact_pending(pool) is not None

    async def test_user_pending_has_priority(self, pool, patch_io, monkeypatch):
        """用户 thread 自己的 HITL pending 优先于中介 pending"""
        from agent.db.interaction_dao import CONTACT_THREAD_PREFIX, save_pending

        patch_io["messages"] = [msg(0, "历史消息")]
        state = InteractionState()
        await poll_interaction(pool, FakeAgent(), cfg(), state)

        await save_pending(pool, THREAD, CONV, {
            "action_requests": [{"name": "export_job_report"}],
        })
        await save_pending(pool, f"{CONTACT_THREAD_PREFIX}小王", "小王", {"mode": "draft"})
        agent = FakeAgent(reply="报告已生成")

        patch_io["messages"].append(msg(1, "同意", t="11:00"))
        result = await poll_interaction(pool, agent, cfg(), state)

        assert result.get("pending") is True
        inputs, _ = agent.calls[-1]
        assert isinstance(inputs, Command)
        assert inputs.resume == {"decisions": [{"type": "approve"}]}

    async def test_contact_pending_not_blocked_by_rate_limit(self, pool, patch_io, monkeypatch):
        """审批回复不受每小时限流影响（否则用户无法确认中介草稿）"""
        from agent.db.interaction_dao import CONTACT_THREAD_PREFIX, save_pending

        patch_io["messages"] = [msg(0, "历史消息")]
        state = InteractionState()
        await poll_interaction(pool, FakeAgent(), cfg(), state)  # 基线

        await save_pending(pool, f"{CONTACT_THREAD_PREFIX}小王", "小王", {"mode": "draft"})
        state.reply_times = [__import__("time").time()] * 5  # 打满限流

        async def fake_resolve(pool_arg, agent, pending, decision, pending_ttl_min=1440):
            return {"ok": True, "message": "已处理", "contact_name": "小王"}

        import agent.contact_interaction as contact_mod

        monkeypatch.setattr(contact_mod, "resolve_contact_pending", fake_resolve)

        patch_io["messages"].append(msg(1, "同意", t="11:00"))
        agent = FakeAgent()
        result = await poll_interaction(pool, agent, cfg(max_replies_per_hour=5), state)
        assert result.get("contact_pending") is True
