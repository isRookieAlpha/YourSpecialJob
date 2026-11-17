# -*- coding: utf-8 -*-
"""会话路由：GET/POST /api/sessions、GET /{id}/messages、DELETE /api/sessions/{id}（消息历史在 checkpointer）。"""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from agent.db.sessions_dao import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
)
from backend.deps import get_pool
from backend.history import convert_history_messages, convert_history_todos

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    """创建会话请求"""

    title: str | None = Field(default=None, description="标题，缺省为 新会话")


@router.get("")
async def get_sessions(request: Request):
    """会话列表（按最近更新倒序）"""
    return await list_sessions(get_pool(request))


@router.post("", status_code=201)
async def post_session(body: SessionCreate, request: Request):
    """显式创建会话"""
    return await create_session(get_pool(request), uuid4().hex, body.title)


@router.get("/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request):
    """历史消息回放：从 checkpointer 读取该 thread 的消息与 todo 状态

    会话不存在→404；thread 无 checkpoint→空数组；agent 未就绪→503。
    """
    pool = get_pool(request)
    if await get_session(pool, session_id) is None:
        raise HTTPException(404, detail="会话不存在")

    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(503, detail=f"Agent 初始化失败：{request.app.state.startup_error}")

    config = {"configurable": {"thread_id": session_id}}
    snapshot = await agent.aget_state(config)
    values = snapshot.values or {}
    return {
        "messages": convert_history_messages(values.get("messages", [])),
        "todos": convert_history_todos(values),
    }


@router.delete("/{session_id}", status_code=204)
async def remove_session(session_id: str, request: Request):
    """删除会话（元数据 + best-effort 删除 checkpointer 历史）"""
    deleted = await delete_session(get_pool(request), session_id)
    if not deleted:
        raise HTTPException(404, detail="会话不存在")

    # checkpointer 历史清理为 best-effort：失败只记日志，不影响删除结果
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        try:
            await checkpointer.adelete_thread(session_id)
        except Exception:
            logger.warning("checkpointer 历史删除失败 thread={}", session_id, exc_info=True)
    return None