# -*- coding: utf-8 -*-
"""微信发送工具：给中介/招聘方联系人发送消息（由 HITL 中间件拦截确认）。

发送成功后：
- 写入出站账本（wechat_seen_messages，group_name=outbound:{联系人}），
  供中介互动轮询过滤机器人自己发出的消息，避免自触发；
- 自动把联系人追加到 preferences.wechat_contacts（用户确认过的中介名单）。
"""
from datetime import date

from langchain_core.tools import tool
from loguru import logger

from agent.config import get_settings
from agent.db.pool import get_pool
from agent.db.settings_dao import add_wechat_contact
from agent.db.tracking_dao import record_outbound_message
from agent.mcp_wechat import WechatMcpError, send_wechat_message
from agent.preferences import resolve_wechat_groups
from agent.wechat_text import fit_wechat_message, to_wechat_plain_text
from agent.wechat_window import wechat_window_lock

MAX_SEND_LEN = 1800


async def deliver_to_contact(pool, contact_name: str, message: str) -> str:
    """校验并发送消息给联系人（工具与后台确认流程共用）

    Args:
        pool: 数据库连接池（发送成功后写账本/名单）
        contact_name: 微信好友备注/昵称
        message: 消息正文（发送前会做纯文本化与长度限制）

    Returns:
        中文结果文本；发送失败时包含原因与草稿
    """
    contact_name = str(contact_name or "").strip()
    body = str(message or "").strip()
    if not contact_name:
        return "联系人不能为空，请提供微信好友备注或昵称。"
    if not body:
        return "消息内容不能为空。"

    #1.目标安全校验：不能发给机器人自己的互动会话，也不能发到监控群
    prefs = get_settings().preferences or {}
    target = (
        str(prefs.get("wechat_target") or "").strip()
        or get_settings().wechat_sender_id.strip()
    )
    if target and contact_name == target:
        return f"不能发送给机器人的互动会话「{contact_name}」，请换一个联系人。"
    if contact_name in resolve_wechat_groups(prefs):
        return f"不能发送到监控群「{contact_name}」，请换一个联系人。"

    #2.微信端只支持纯文本，统一清洗并限制长度
    plain = to_wechat_plain_text(body)
    text = fit_wechat_message("", plain, MAX_SEND_LEN, suffix="\n…（已截断）")

    try:
        async with wechat_window_lock:
            await send_wechat_message(contact_name, text)
    except WechatMcpError as e:
        logger.warning("发给联系人的消息发送失败 contact={}：{}", contact_name, e)
        return (
            f"发送失败：{e}\n请确认 PC 微信已登录、联系人名称正确。"
            f"草稿如下，可稍后手动发送：\n{text}"
        )
    except Exception as e:
        logger.exception("发给联系人的消息发送异常")
        return f"发送失败：{type(e).__name__}。草稿如下，可稍后手动发送：\n{text}"

    #3.出站账本 + 中介名单（均失败不影响发送结果）
    try:
        await record_outbound_message(pool, contact_name, date.today(), text)
    except Exception as e:
        logger.warning("出站账本写入失败 contact={}：{}", contact_name, e)
    try:
        await add_wechat_contact(pool, contact_name)
    except Exception as e:
        logger.warning("中介名单追加失败 contact={}：{}", contact_name, e)

    logger.info("已发送给联系人 {}（{} 字）", contact_name, len(text))
    return f"已发送给「{contact_name}」。可在微信中查看该会话。"


@tool(parse_docstring=True)
async def send_wechat_message_to_contact(contact_name: str, message: str) -> str:
    """把消息发送给指定的微信联系人（中介/招聘方）。执行前系统会请求用户确认。

    仅在用户明确要求"发给 X / 回复中介 X"时调用；不要向用户自己的互动会话或监控群发送。
    消息内容应使用 search_resume 检索到的真实简历信息，不要编造。

    Args:
        contact_name: 微信联系人名称（好友备注/昵称），如 "中介小王"。
        message: 消息正文（纯文本，≤1800 字）。

    Returns:
        发送结果；失败时返回中文原因与草稿（可让用户稍后手动发送）。
    """
    return await deliver_to_contact(await get_pool(), contact_name, message)