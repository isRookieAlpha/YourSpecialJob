# -*- coding: utf-8 -*-
"""send_wechat_message_to_contact 工具测试（mock 发送与偏好，使用测试库写账本）。"""
from datetime import date
from types import SimpleNamespace

import agent.tools.wechat_send as tool_mod
from agent.db.settings_dao import get_raw_settings
from agent.mcp_wechat import WechatMcpError
from agent.tools.wechat_send import send_wechat_message_to_contact
from agent.db.tracking_dao import filter_outbound


def patch_settings(monkeypatch, prefs, sender_id="Alpha"):
    monkeypatch.setattr(
        tool_mod,
        "get_settings",
        lambda: SimpleNamespace(preferences=prefs, wechat_sender_id=sender_id),
    )


def patch_pool(monkeypatch, pool):
    async def fake_get_pool():
        return pool

    monkeypatch.setattr(tool_mod, "get_pool", fake_get_pool)


class TestSendToContact:
    async def test_success_plain_text(self, monkeypatch, pool):
        sent: dict = {}

        async def fake_send(contact, text):
            sent["contact"] = contact
            sent["text"] = text

        patch_settings(monkeypatch, {"wechat_target": "Alpha", "wechat_groups": ["兼职1群"]})
        patch_pool(monkeypatch, pool)
        monkeypatch.setattr(tool_mod, "send_wechat_message", fake_send)

        text = await send_wechat_message_to_contact.ainvoke({
            "contact_name": "中介小王",
            "message": "**您好**，我是小明，做过活动执行。",
        })
        assert "已发送给" in text
        assert sent["contact"] == "中介小王"
        assert "**" not in sent["text"]
        assert "您好" in sent["text"]
        # 出站账本：下一轮轮询会过滤这条消息
        hits = await filter_outbound(pool, "中介小王", date.today(), [sent["text"]])
        assert hits == {sent["text"]}
        # 中介名单：发送成功后自动加入
        data = await get_raw_settings(pool)
        assert "中介小王" in (data.get("preferences") or {}).get("wechat_contacts", [])

    async def test_reject_interaction_target(self, monkeypatch):
        patch_settings(monkeypatch, {"wechat_target": "Alpha"})
        patch_pool(monkeypatch, None)
        text = await send_wechat_message_to_contact.ainvoke({
            "contact_name": "Alpha", "message": "hi",
        })
        assert "不能发送给机器人的互动会话" in text

    async def test_reject_monitored_group(self, monkeypatch):
        patch_settings(monkeypatch, {"wechat_target": "Alpha", "wechat_groups": ["兼职1群"]})
        patch_pool(monkeypatch, None)
        text = await send_wechat_message_to_contact.ainvoke({
            "contact_name": "兼职1群", "message": "hi",
        })
        assert "监控群" in text

    async def test_failure_returns_draft(self, monkeypatch):
        async def failing(contact, text):
            raise WechatMcpError("发送超时（>90s）")

        patch_settings(monkeypatch, {"wechat_target": "Alpha"})
        patch_pool(monkeypatch, None)
        monkeypatch.setattr(tool_mod, "send_wechat_message", failing)

        text = await send_wechat_message_to_contact.ainvoke({
            "contact_name": "中介小王", "message": "您好，想报名这个兼职。",
        })
        assert "发送失败" in text
        assert "草稿" in text
        assert "您好" in text

    async def test_empty_contact(self, monkeypatch):
        patch_pool(monkeypatch, None)
        text = await send_wechat_message_to_contact.ainvoke({
            "contact_name": " ", "message": "hi",
        })
        assert "联系人不能为空" in text