# -*- coding: utf-8 -*-
"""聊天路由：POST /api/chat/stream（SSE 流式输出）+ POST /api/chat/resume（HITL 恢复）。"""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agent.db.sessions_dao import upsert_session_on_message
from agent.memory.preference_extractor import extract_and_store_memory
from backend.history import convert_history_messages
from backend.sse import sse_events


def _memory_hook(app, thread_id: str):
    """构造长期记忆提取回调（done 后 fire-and-forget，失败不影响聊天）"""

    async def hook(user_message: str) -> None:
        store = getattr(app.state, "store", None)
        agent = getattr(app.state, "agent", None)
        if store is None or agent is None or not user_message:
            return
        assistant = ""
        try:
            snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
            converted = convert_history_messages((snapshot.values or {}).get("messages", []))
            for m in reversed(converted):
                if m["role"] == "assistant":
                    assistant = m["content"]
                    break
        except Exception:
            logger.warning("读取最终回复失败（记忆提取继续） thread={}", thread_id, exc_info=True)
        await extract_and_store_memory(store, thread_id, user_message, assistant)

    return hook

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """聊天请求：thread_id 缺省时新建会话"""

    thread_id: str | None = Field(default=None, description="会话 id（checkpointer 的 thread_id）")
    message: str = Field(description="用户消息")


class ResumeRequest(BaseModel):
    """HITL 恢复请求：decisions 数量必须与挂起工具调用数一致"""

    thread_id: str = Field(description="会话 id")
    decisions: list[dict] = Field(description="审批决策：[{type: approve|reject|edit, ...}]")


@router.post("/stream")
async def chat_stream(body: ChatRequest, request: Request):
    """流式聊天（SSE 事件：meta/token/todos/tool/progress/hitl/done/error）"""
    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(
            503, detail=f"Agent 初始化失败：{request.app.state.startup_error}"
        )
    if not body.message.strip():
        raise HTTPException(400, detail="消息不能为空")

    thread_id = body.thread_id or uuid4().hex

    # 会话元数据：首条消息自动建会话并取标题（失败不影响聊天）
    try:
        await upsert_session_on_message(request.app.state.pool, thread_id, body.message)
    except Exception:
        logger.warning("会话元数据写入失败 thread={}", thread_id, exc_info=True)

    return EventSourceResponse(
        sse_events(
            agent, thread_id,
            {"messages": [{"role": "user", "content": body.message}]},
            user_message=body.message,
            on_complete=_memory_hook(request.app, thread_id),
        ),
        ping=15,  # 15s 心跳保活，防代理断连
    )


@router.post("/resume")
async def chat_resume(body: ResumeRequest, request: Request):
    """HITL 恢复：审批后继续流式输出（SSE 事件同 stream）"""
    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(
            503, detail=f"Agent 初始化失败：{request.app.state.startup_error}"
        )
    if not body.decisions:
        raise HTTPException(400, detail="decisions 不能为空")

    #1.校验该 thread 确实处于中断状态（无 checkpoint / 已恢复 → 400）
    config = {"configurable": {"thread_id": body.thread_id}}
    snapshot = await agent.aget_state(config)
    if not snapshot.interrupts:
        raise HTTPException(400, detail="该会话没有待确认的操作（可能已处理或已过期）")

    #2.decisions 数量必须与挂起工具调用数匹配（HITL 中间件源码有硬校验，提前给中文错误）
    value = snapshot.interrupts[0].value or {}
    expected = len(value.get("action_requests", [])) if isinstance(value, dict) else 0
    if expected and len(body.decisions) != expected:
        raise HTTPException(
            400, detail=f"决策数量不匹配：待确认操作 {expected} 个，收到 {len(body.decisions)} 个"
        )

    # 恢复前 snapshot 中取最后一条用户消息（作为记忆提取的 user_message）
    converted = convert_history_messages((snapshot.values or {}).get("messages", []))
    last_user = next((m["content"] for m in reversed(converted) if m["role"] == "user"), "")

    return EventSourceResponse(
        sse_events(
            agent, body.thread_id,
            Command(resume={"decisions": body.decisions}),
            user_message=last_user,
            on_complete=_memory_hook(request.app, body.thread_id),
        ),
        ping=15,
    )