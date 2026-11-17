# -*- coding: utf-8 -*-
"""微信互动状态路由：用户互动 + 中介互动（状态/待确认/确认/立即执行）。"""
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from agent.contact_interaction import _load_configs, resolve_contact_pending
from agent.db.interaction_dao import (
    CONTACT_THREAD_PREFIX,
    claim_pending,
    get_state,
    list_contact_pending,
    list_states,
)
from agent.interaction import _load_config
from backend.deps import get_pool

router = APIRouter(prefix="/api/interaction", tags=["interaction"])


def _mask(value: str) -> str:
    if not value:
        return ""
    return value if len(value) <= 2 else value[0] + "***" + value[-1]


def _public_pending(pending: dict) -> dict:
    """中介待确认的对外字段（不返回原始 action_requests）"""
    payload = pending.get("interrupt_payload") or {}
    return {
        "thread_id": pending.get("thread_id"),
        "contact_name": pending.get("conversation") or payload.get("contact_name") or "",
        "source_message": payload.get("source_message") or "",
        "draft": payload.get("draft") or "",
        "mode": payload.get("mode") or "hitl",
        "created_at": pending.get("created_at"),
        "expires_at": pending.get("expires_at"),
    }


@router.get("/status")
async def interaction_status(request: Request):
    """互动 worker 状态（不返回完整会话名）"""
    pool = get_pool(request)
    cfg = await _load_config(pool)
    state = getattr(request.app.state, "interaction_state", None)

    pending_count = 0
    conversation_state = None
    if cfg.target:
        try:
            async with pool.connection() as conn:
                cur = await conn.execute("SELECT count(*) FROM interaction_pending")
                pending_count = (await cur.fetchone())[0]
            conversation_state = await get_state(pool, cfg.target)
        except Exception:
            logger.warning("读取互动状态失败", exc_info=True)

    return {
        "enabled": cfg.enabled,
        "wechat_access_enabled": cfg.wechat_access_enabled,
        "running": state.running if state else False,
        "interval_sec": cfg.interval_sec,
        "target_configured": bool(cfg.target),
        "target_masked": _mask(cfg.target),
        "paused_reason": cfg.paused_reason,
        "pending_count": pending_count,
        "conversation": conversation_state,
        "last_poll": state.snapshot() if state else None,
    }


@router.post("/run", status_code=202)
async def interaction_run(request: Request):
    """立即检查一次互动会话（唤醒调度器，不改变启用状态）"""
    cfg = await _load_config(get_pool(request))
    if not cfg.wechat_access_enabled:
        raise HTTPException(403, detail="微信访问已在设置页关闭")
    wakeup = getattr(request.app.state, "interaction_wakeup", None)
    if wakeup is None:
        raise HTTPException(503, detail="互动任务未启动（服务可能处于降级状态）")
    wakeup.set()
    logger.info("收到手动触发互动请求")
    return {"accepted": True}


# ---------- 中介互动 ----------


@router.get("/contact-status")
async def contact_interaction_status(request: Request):
    """中介互动 worker 状态（名单 / 运行状态 / 待确认）"""
    pool = get_pool(request)
    try:
        cfg, _ = await _load_configs(pool)
        pending_items = await list_contact_pending(pool)
        states = await list_states(pool, cfg.contacts)
    except Exception:
        logger.warning("读取中介互动状态失败", exc_info=True)
        raise HTTPException(503, detail="读取中介互动状态失败")

    state = getattr(request.app.state, "contact_interaction_state", None)
    return {
        "enabled": cfg.enabled,
        "wechat_access_enabled": cfg.wechat_access_enabled,
        "running": state.running if state else False,
        "interval_sec": cfg.interval_sec,
        "contacts": cfg.contacts,
        "paused_reason": cfg.paused_reason,
        "pending_count": len(pending_items),
        "pending": [_public_pending(p) for p in pending_items],
        "conversations": states,
        "last_poll": state.snapshot() if state else None,
    }


@router.post("/contact-run", status_code=202)
async def contact_interaction_run(request: Request):
    """立即检查一个中介会话（轮转，不改变启用状态）"""
    cfg, _ = await _load_configs(get_pool(request))
    if not cfg.wechat_access_enabled:
        raise HTTPException(403, detail="微信访问已在设置页关闭")
    wakeup = getattr(request.app.state, "contact_interaction_wakeup", None)
    if wakeup is None:
        raise HTTPException(503, detail="中介互动任务未启动（服务可能处于降级状态）")
    wakeup.set()
    logger.info("收到手动触发中介互动请求")
    return {"accepted": True}


@router.get("/contact-pending")
async def contact_pending_list(request: Request):
    """中介待确认列表（网页聊天页浮动卡片用）"""
    pool = get_pool(request)
    items = await list_contact_pending(pool)
    return {"items": [_public_pending(p) for p in items]}


class ContactPendingResume(BaseModel):
    """中介待确认的确认请求"""

    thread_id: str
    decision: str  # approve / reject


@router.post("/contact-pending/resume")
async def contact_pending_resume(body: ContactPendingResume, request: Request):
    """批准/拒绝中介待确认（与微信端共用原子领取，避免重复发送）"""
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, detail="decision 必须是 approve 或 reject")
    if not body.thread_id.startswith(CONTACT_THREAD_PREFIX):
        raise HTTPException(409, detail="不是中介待确认操作")
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(503, detail="agent 未就绪（服务可能处于降级状态）")

    pool = get_pool(request)
    claimed = await claim_pending(pool, body.thread_id)
    if claimed is None:
        raise HTTPException(404, detail="待确认操作不存在或已被另一端处理")
    if claimed.get("expired"):
        raise HTTPException(410, detail="待确认操作已过期")

    _, interaction_cfg = await _load_configs(pool)
    outcome = await resolve_contact_pending(
        pool, agent, claimed, body.decision,
        pending_ttl_min=interaction_cfg.pending_ttl_min,
    )
    if not outcome.get("ok"):
        raise HTTPException(502, detail=outcome.get("message") or "处理失败")
    logger.info(
        "网页端处理中介待确认 contact={} decision={}",
        outcome.get("contact_name"), body.decision,
    )
    return outcome