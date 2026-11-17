# -*- coding: utf-8 -*-
"""通勤重算路由：POST /api/commute/refresh、GET /api/commute/refresh/status。"""
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from agent.config import get_settings

router = APIRouter(prefix="/api/commute", tags=["commute"])


@router.post("/refresh", status_code=202)
async def refresh_commute(request: Request):
    """触发全库通勤重算（运行中再次触发则排队再跑一轮）"""
    state = getattr(request.app.state, "commute_refresh_state", None)
    wakeup = getattr(request.app.state, "commute_refresh_wakeup", None)
    if state is None or wakeup is None:
        raise HTTPException(503, detail="通勤重算任务未启动（服务可能处于降级状态）")

    prefs = get_settings().preferences or {}
    origin = str(prefs.get("commute_origin") or "").strip()
    if not origin:
        raise HTTPException(400, detail="未配置通勤起始位置，无法重算")

    queued = bool(state.running)
    if queued:
        state.pending = True
    else:
        wakeup.set()
    logger.info("收到通勤重算请求（queued={}）", queued)
    return {"accepted": True, "queued": queued}


@router.get("/refresh/status")
async def refresh_status(request: Request):
    """重算进度（不含密钥）"""
    state = getattr(request.app.state, "commute_refresh_state", None)
    if state is None:
        return {"running": False, "pending": False, "last_error": "任务未启动"}
    return state.snapshot()