# -*- coding: utf-8 -*-
"""长期跟踪状态路由：GET /api/tracking/status、POST /api/tracking/run（立即执行一轮）。"""
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from agent.config import get_settings
from agent.db.tracking_dao import list_group_states, push_stats_since
from agent.tracking import load_config
from backend.deps import get_pool

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


@router.get("/status")
async def tracking_status(request: Request):
    """跟踪器状态（不返回密钥与完整 sender）"""
    pool = get_pool(request)
    cfg = await load_config(pool)
    state = getattr(request.app.state, "tracking_state", None)
    groups = await list_group_states(pool)
    push_stats = await push_stats_since(pool)

    return {
        "enabled": cfg.enabled,
        "wechat_access_enabled": cfg.wechat_access_enabled,
        "running": state.running if state else False,
        "interval_min": cfg.interval_min,
        "groups_configured": cfg.groups,
        "push_enabled": cfg.push_enabled,
        "push_target_configured": bool(get_settings().wechat_sender_id.strip()),
        "amap_configured": bool(get_settings().amap_server_key),
        "commute_origin_configured": bool(cfg.commute_origin),
        "paused_reason": cfg.paused_reason,
        "progress": dict(state.progress) if state else {"current": 0, "total": 0, "label": ""},
        "worker_pid": state.worker_pid if state else None,
        "last_round": {
            "at": state.last_run_at,
            "ok": state.last_ok,
            "stats": state.last_stats,
            "error": state.last_error,
        } if state else None,
        "groups": groups,
        "push_24h": push_stats,
    }


@router.post("/run", status_code=202)
async def tracking_run(request: Request):
    """立即执行一轮（唤醒调度器，不改变启用状态）"""
    cfg = await load_config(get_pool(request))
    if not cfg.wechat_access_enabled:
        raise HTTPException(403, detail="微信访问已在设置页关闭")
    wakeup = getattr(request.app.state, "tracking_wakeup", None)
    if wakeup is None:
        raise HTTPException(503, detail="跟踪任务未启动（服务可能处于降级状态）")
    wakeup.set()
    logger.info("收到手动触发跟踪请求")
    return {"accepted": True}