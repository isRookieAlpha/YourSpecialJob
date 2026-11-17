# -*- coding: utf-8 -*-
"""中介互动 worker 测试（mock 微信读取/发送，真实测试库账本与 pending）。"""
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command, Interrupt

import agent.contact_interaction as contact_mod
from agent.contact_interaction import (
    ContactInteractionState,
    build_contact_pending_notice,
    pick_next_contact,
    poll_contact,
    resolve_contact_pending,
)
from agent.db.interaction_dao import (
    CONTACT_THREAD_PREFIX,
    ensure_state,
    get_contact_pending,
    get_state,
    save_pending,
    touch_state,
)
from agent.db.tracking_dao import message_fingerprint, record_outbound_message, record_seen
from agent.preferences import ContactInteractionConfig

CONTACT = "小王"
THREAD = f"{CONTACT_THREAD_PREFIX}{CONTACT}"


async def seed_baseline(pool, messages: list[dict]) -> None:
    """直接建立已完成基线（避免基线草拟生成 pending 干扰后续断言）"""
    day = date.today()
    ledger = f"interaction:{CONTACT}"
    await ensure_state(pool, CONTACT)
    await record_seen(pool, ledger, day, [
        {
            "fingerprint": message_fingerprint(
                ledger, day, m.get("发送者", ""), m.get("时间", ""), m.get("消息", "")
            ),
            "sender": m.get("发送者"),
            "msg_time": m.get("时间"),
        }
        for m in messages
    ])
    from agent.db.interaction_dao import mark_initialized

    await mark_initialized(pool, CONTACT)


def cfg(**kwargs) -> ContactInteractionConfig:
    base = dict(enabled=True, interval_sec=60, contacts=[CONTACT], wechat_access_enabled=True)
    base.update(kwargs)
    return ContactInteractionConfig(**base)


def msg(i: int, text: str, t: str = "10:00", sender: str = CONTACT) -> dict:
    return {"index": i, "发送者": sender, "时间": t, "消息": text}


class FakeAgent:
    def __init__(self, reply: str = "好的", interrupt: dict | None = None):
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

    async def fake_read(contact, label, allow_empty=False, timeout=180, search_pages=1):
        return list(state["messages"])

    async def fake_send(pool, conversation, text, state_obj=None, **kwargs):
        state["sent"].append(text)
        return True

    monkeypatch.setattr(contact_mod, "call_wechat_get_history", fake_read)
    monkeypatch.setattr(contact_mod, "_send_reply", fake_send)
    return state


class TestSkipConditions:
    async def test_wechat_access_disabled(self, pool, patch_io):
        result = await poll_contact(
            pool, FakeAgent(), cfg(wechat_access_enabled=False), ContactInteractionState()
        )
        assert result["reason"] == "微信访问已关闭"
        assert patch_io["sent"] == []

    async def test_disabled(self, pool, patch_io):
        result = await poll_contact(
            pool, FakeAgent(), cfg(enabled=False), ContactInteractionState()
        )
        assert result["reason"] == "中介互动未启用"

    async def test_no_contacts(self, pool, patch_io):
        result = await poll_contact(
            pool, FakeAgent(), cfg(contacts=[]), ContactInteractionState()
        )
        assert result["reason"] == "未配置中介名单"

    async def test_agent_missing(self, pool, patch_io):
        result = await poll_contact(pool, None, cfg(), ContactInteractionState())
        assert result["reason"] == "agent 未就绪"

    async def test_existing_pending_skips_read(self, pool, patch_io):
        await save_pending(pool, THREAD, CONTACT, {"mode": "draft", "draft": "hi"})
        agent = FakeAgent()
        result = await poll_contact(pool, agent, cfg(), ContactInteractionState())
        assert result["skipped"] is True
        assert result["reason"] == "有待确认的中介回复"
        assert agent.calls == []


class TestPolling:
    async def test_baseline_also_drafts(self, pool, patch_io):
        """基线轮也草拟（用户要求）：历史消息取最近 N 条生成草稿"""
        patch_io["messages"] = [msg(0, "历史消息一"), msg(1, "历史消息二", t="10:30")]
        agent = FakeAgent(reply="您好，我是小明。")
        result = await poll_contact(
            pool, agent, cfg(), ContactInteractionState(), notify_target="Alpha"
        )
        assert result["baseline"] is True
        assert result["pending"] is True and result["mode"] == "draft"
        assert len(agent.calls) == 1
        prompt = agent.calls[0][0]["messages"][0]["content"]
        assert "历史消息一" in prompt and "历史消息二" in prompt
        state_row = await get_state(pool, CONTACT)
        assert state_row["initialized_at"] is not None
        pending = await get_contact_pending(pool)
        assert pending["interrupt_payload"]["draft"] == "您好，我是小明。"

    async def test_baseline_uses_latest_three(self, pool, patch_io):
        patch_io["messages"] = [msg(i, f"消息{i}", t=f"10:0{i}") for i in range(5)]
        agent = FakeAgent(reply="草稿")
        await poll_contact(pool, agent, cfg(), ContactInteractionState())
        prompt = agent.calls[0][0]["messages"][0]["content"]
        assert "消息4" in prompt and "消息2" in prompt
        assert "消息1" not in prompt and "消息0" not in prompt

    async def test_new_message_creates_hitl_pending(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        await seed_baseline(pool, patch_io["messages"])
        state_obj = ContactInteractionState()

        interrupt = {
            "action_requests": [{
                "name": "send_wechat_message_to_contact",
                "args": {"contact_name": CONTACT, "message": "您好，我是小明，做过活动执行。"},
            }],
            "review_configs": [],
        }
        agent = FakeAgent(reply="已提交草稿", interrupt=interrupt)
        patch_io["messages"].append(msg(1, "明天能来面试吗", t="11:00"))

        result = await poll_contact(pool, agent, cfg(), state_obj, notify_target="Alpha")
        assert result["pending"] is True and result["mode"] == "hitl"
        pending = await get_contact_pending(pool)
        assert pending["conversation"] == CONTACT
        payload = pending["interrupt_payload"]
        assert payload["mode"] == "hitl"
        assert payload["draft"] == "您好，我是小明，做过活动执行。"
        assert "明天能来面试吗" in payload["source_message"]
        assert patch_io["sent"] and "中介回复待确认" in patch_io["sent"][-1]

    async def test_text_fallback_draft_pending(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        await seed_baseline(pool, patch_io["messages"])
        state_obj = ContactInteractionState()

        agent = FakeAgent(reply="您好，我明天下午可以到岗。")
        patch_io["messages"].append(msg(1, "明天能来吗", t="11:00"))
        result = await poll_contact(pool, agent, cfg(), state_obj, notify_target="Alpha")

        assert result["mode"] == "draft"
        pending = await get_contact_pending(pool)
        assert pending["interrupt_payload"]["draft"] == "您好，我明天下午可以到岗。"

    async def test_outbound_message_filtered(self, pool, patch_io):
        """出站账本过滤机器人自己发出的消息（不触发自回复）"""
        patch_io["messages"] = [msg(0, "历史消息")]
        await seed_baseline(pool, patch_io["messages"])
        state_obj = ContactInteractionState()

        sent_text = "您好，我是小明。"
        await record_outbound_message(pool, CONTACT, date.today(), sent_text)
        patch_io["messages"].append(msg(1, sent_text, t="11:00", sender="Alpha"))
        agent = FakeAgent()
        result = await poll_contact(pool, agent, cfg(), state_obj)

        assert result["new_messages"] == 0
        assert agent.calls == []

    async def test_robot_prefix_filtered(self, pool, patch_io):
        patch_io["messages"] = [msg(0, "历史消息")]
        await seed_baseline(pool, patch_io["messages"])
        state_obj = ContactInteractionState()

        patch_io["messages"].append(msg(1, "【YourSpecialJob 回复】系统消息", t="11:00"))
        result = await poll_contact(pool, FakeAgent(), cfg(), state_obj)
        assert result["new_messages"] == 0

    async def test_pick_next_contact_oldest_first(self, pool):
        await ensure_state(pool, "小王")
        await ensure_state(pool, "张姐")
        await touch_state(pool, "张姐")
        assert await pick_next_contact(pool, ["小王", "张姐"]) == "小王"


class TestResolvePending:
    async def test_draft_approve_sends(self, pool, monkeypatch):
        calls = []

        async def fake_deliver(pool_arg, contact, message):
            calls.append((contact, message))
            return f"已发送给「{contact}」。"

        monkeypatch.setattr(contact_mod, "deliver_to_contact", fake_deliver)
        pending = {
            "thread_id": THREAD,
            "conversation": CONTACT,
            "interrupt_payload": {"mode": "draft", "draft": "您好"},
            "expired": False,
        }
        result = await resolve_contact_pending(None, None, pending, "approve")
        assert result["ok"] is True
        assert calls == [(CONTACT, "您好")]

    async def test_draft_reject_does_not_send(self, pool, monkeypatch):
        calls = []

        async def fake_deliver(pool_arg, contact, message):
            calls.append((contact, message))
            return "sent"

        monkeypatch.setattr(contact_mod, "deliver_to_contact", fake_deliver)
        pending = {
            "thread_id": THREAD,
            "conversation": CONTACT,
            "interrupt_payload": {"mode": "draft", "draft": "您好"},
            "expired": False,
        }
        result = await resolve_contact_pending(None, None, pending, "reject")
        assert result["ok"] is True
        assert calls == []

    async def test_hitl_approve_resumes_thread(self, pool):
        agent = FakeAgent(reply="已发送给「小王」。")
        pending = {
            "thread_id": THREAD,
            "conversation": CONTACT,
            "interrupt_payload": {
                "mode": "hitl",
                "action_requests": [{"name": "send_wechat_message_to_contact", "args": {}}],
            },
            "expired": False,
        }
        result = await resolve_contact_pending(pool, agent, pending, "approve")
        assert result["ok"] is True
        assert result["message"] == "已发送给「小王」。"
        inputs, config = agent.calls[0]
        assert isinstance(inputs, Command)
        assert inputs.resume == {"decisions": [{"type": "approve"}]}
        assert config["configurable"]["thread_id"] == THREAD

    async def test_hitl_reject_resumes_reject(self, pool):
        agent = FakeAgent(reply="已取消")
        pending = {
            "thread_id": THREAD,
            "conversation": CONTACT,
            "interrupt_payload": {
                "mode": "hitl",
                "action_requests": [{"name": "send_wechat_message_to_contact", "args": {}}],
            },
            "expired": False,
        }
        await resolve_contact_pending(pool, agent, pending, "reject")
        inputs, _ = agent.calls[0]
        assert inputs.resume["decisions"][0]["type"] == "reject"

    async def test_expired_pending_rejected(self):
        pending = {
            "thread_id": THREAD,
            "conversation": CONTACT,
            "interrupt_payload": {"mode": "draft", "draft": "hi"},
            "expired": True,
        }
        result = await resolve_contact_pending(None, None, pending, "approve")
        assert result["ok"] is False
        assert "过期" in result["message"]


class TestNotice:
    def test_notice_contains_contact_and_draft(self):
        notice = build_contact_pending_notice({
            "contact_name": "小王",
            "source_message": "[11:00] 明天能来吗",
            "draft": "您好，我明天下午可以到岗。",
        })
        assert "中介回复待确认" in notice
        assert "联系人：小王" in notice
        assert "明天能来吗" in notice
        assert "您好，我明天下午可以到岗。" in notice
        assert "同意" in notice and "拒绝" in notice