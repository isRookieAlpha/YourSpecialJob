# -*- coding: utf-8 -*-
"""
astream(["messages","updates","custom"]) → SSE 事件映射。

probe_astream.py 实测形状（2026-11-03，qwen3.5-omni-plus）：
- astream 逐项 yield (mode, payload) 二元组
- messages 模式 payload=(chunk, metadata)，metadata["langgraph_node"] 为 "model"/"tools" 等
  · token 增量 = AIMessageChunk + node=="model" + 非空 content
  · ToolMessage 在 messages 与 updates 双通道都会出现（此处只从 updates 取，避免重复）
- updates 模式 payload={节点名: 状态增量 dict | None}
  · todos 全量快照出现在 tools 节点的增量里：{"todos": [...], "messages": [...]}
  · 中间件节点（SummarizationMiddleware.before_model / TodoListMiddleware.after_model）增量多为 None

probe_hitl.py 实测形状（2026-11-04，HITL 中断/恢复）：
- 中断出现在 updates 的顶层键 "__interrupt__"，值为 Interrupt 元组：
  {"__interrupt__": (Interrupt(value={"action_requests": [...],
                                      "review_configs": [...]}, id="..."),)}
- action_requests 元素：{"name": 工具名, "args": {...}, "description": "..."}
- review_configs 元素：{"action_name": 工具名, "allowed_decisions": ["approve","edit","reject"]}
- approve 恢复（Command(resume={"decisions":[{"type":"approve"}]})）后：
  工具在 tools 节点真实执行 → ToolMessage → token 流 → 最终回复
- reject 恢复（{"type":"reject","message":"..."}）：
  HumanInTheLoopMiddleware.after_model 产出 ToolMessage(content=拒绝理由) → agent 解释已取消
"""
import asyncio
import json
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk, ToolMessage
from loguru import logger

# 长期记忆提取等后台任务的强引用（防止被 GC）
_background_tasks: set[asyncio.Task] = set()


def _sse(event: str, data: dict) -> dict:
    """构造 sse-starlette 事件 dict"""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


async def sse_events(
    agent, thread_id: str, inputs, *,
    user_message: str | None = None,
    on_complete=None,
) -> AsyncIterator[dict]:
    """把 agent 流式输出映射为 SSE 事件序列

    Args:
        agent: 编译后的 agent 图
        thread_id: 会话 id（checkpointer 的 thread_id）
        inputs: {"messages": [...]}（新消息）或 Command(resume=...)（HITL 恢复）
        user_message: 本轮用户消息（长期记忆提取用；stream 传原文，resume 传挂起前的用户消息）
        on_complete: 正常结束后的后台回调（async def (user_message: str) -> None）

    事件协议：meta / token / todos / tool / progress / offers / hitl / done / error
    """
    logger.info("SSE 开始 thread={} inputs_type={}", thread_id, type(inputs).__name__)
    yield _sse("meta", {"thread_id": thread_id})
    config = {"configurable": {"thread_id": thread_id}}
    #1.统计输出情况：用于识别"模型静默失败"（ModelRetry continue 会吞掉 401/403 等错误）
    token_count = 0
    tool_events = 0
    hitl_seen = False
    try:
        async for mode, payload in agent.astream(
            inputs,
            config=config,
            stream_mode=["messages", "updates", "custom"],
        ):
            if mode == "messages":
                if not (isinstance(payload, tuple) and len(payload) == 2):
                    continue
                chunk, metadata = payload
                node = (metadata or {}).get("langgraph_node")
                #2.只转发主模型输出（摘要中间件的调用发生在 before_model 节点，被过滤掉）
                if isinstance(chunk, AIMessageChunk) and node == "model" and chunk.content:
                    token_count += 1
                    yield _sse("token", {"content": chunk.content})
            elif mode == "custom":
                #2.工具内 get_stream_writer() 推送的 custom 事件
                if isinstance(payload, dict) and payload.get("type") == "progress":
                    yield _sse("progress", {"message": payload.get("message", "")})
                elif isinstance(payload, dict) and payload.get("type") == "offers":
                    #3.query_jobs 推送的结构化候选（前端"心动的offer"侧栏数据源）
                    yield _sse("offers", {
                    "items": payload.get("items", []),
                    "replace": bool(payload.get("replace")),
                })
            elif mode == "updates":
                for node, delta in (payload or {}).items():
                    #3.HITL 中断（probe 实测：值是 Interrupt 元组，不是 dict）
                    if node == "__interrupt__":
                        hitl_seen = True
                        for it in delta or ():
                            value = getattr(it, "value", None) or {}
                            if isinstance(value, dict):
                                yield _sse("hitl", {
                                    "thread_id": thread_id,
                                    "actions": value.get("action_requests", []),
                                    "allowed": [
                                        c.get("allowed_decisions", [])
                                        for c in value.get("review_configs", [])
                                    ],
                                })
                        continue
                    if not isinstance(delta, dict):
                        continue
                    #4.todo 全量快照（每次整包替换）
                    todos = delta.get("todos")
                    if todos is not None:
                        yield _sse("todos", {"items": [
                            {"content": t.get("content", ""), "status": t.get("status", "pending")}
                            for t in todos
                        ]})
                    #5.工具完成事件
                    if node == "tools":
                        for m in delta.get("messages", []) or []:
                            if isinstance(m, ToolMessage):
                                tool_events += 1
                                yield _sse("tool", {
                                    "name": m.name,
                                    "status": "done",
                                    "preview": str(m.content)[:300],
                                })
        #6.静默失败兜底：没有任何 token/工具/中断输出（如模型 401/403 被 ModelRetry continue 吞掉）
        if token_count == 0 and tool_events == 0 and not hitl_seen:
            logger.warning("SSE 无任何模型输出 thread={}（可能模型配额耗尽或配置无效）", thread_id)
            yield _sse("error", {
                "message": "模型未返回任何内容：可能是模型账户额度耗尽或模型配置无效，"
                           "请在设置页检查模型配置与账户余额后重试。"
            })
        #7.正常结束（非 HITL 中断、有模型输出）后 fire-and-forget 长期记忆提取
        if on_complete is not None and token_count > 0 and not hitl_seen:
            try:
                task = asyncio.create_task(on_complete(user_message or ""))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            except Exception:
                logger.warning("启动长期记忆提取任务失败", exc_info=True)
        logger.info("SSE 完成 thread={}", thread_id)
        yield _sse("done", {"thread_id": thread_id})
    except Exception as e:
        logger.exception("SSE 流异常 thread={}", thread_id)
        yield _sse("error", {"message": f"处理失败：{e}"})