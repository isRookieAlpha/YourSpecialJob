# -*- coding: utf-8 -*-
"""
主 agent 装配（create_agent + 中间件栈）。

checkpointer 由调用方注入（FastAPI lifespan 负责其生命周期与 setup），
避免 uvicorn --reload 时连接泄漏；probe 脚本可传 InMemorySaver。
"""
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
)

from agent.memory.memory_prompt import memory_prompt
from agent.models_factory import get_llm_backup, get_llm_strong, get_llm_weak
from agent.prompts.system import build_system_prompt
from agent.tool_call_repair import ToolCallRepairMiddleware
from agent.tools.ingest import ingest_wechat_group_messages
from agent.tools.manual_ingest import ingest_text_messages
from agent.tools.query_jobs import add_to_offers, query_jobs, select_offers
from agent.tools.report import export_job_report
from agent.tools.resume import search_resume
from agent.tools.travel import get_today_weather
from agent.tools.wechat_send import send_wechat_message_to_contact


def build_middleware() -> list:
    """中间件栈：Todo 进度 → 80% 上下文摘要 → 动态记忆提示词 → 模型重试 → 备用模型 fallback → HITL 审批"""
    #1.摘要中间件用弱模型，fraction trigger 依赖强模型 profile 中的上下文窗口
    middleware = [
        TodoListMiddleware(),
        SummarizationMiddleware(
            model=get_llm_weak(),
            trigger=[("fraction", 0.8)],
            keep=("messages", 2),
        ),
        #2.动态 system prompt（显式偏好 + 长期记忆）；放在 ModelRetry 之前，重试不重复检索
        memory_prompt,
        ModelRetryMiddleware(max_retries=2, on_failure="continue"),
    ]
    #2.备用模型未配置时省略 fallback 中间件
    backup = get_llm_backup()
    if backup is not None:
        middleware.append(ModelFallbackMiddleware(backup))
    #3.HITL：仅报告导出需人工确认（其余工具自动放行）；必配 checkpointer + thread_id
    middleware.append(
        HumanInTheLoopMiddleware(
            interrupt_on={
                "export_job_report": {"allowed_decisions": ["approve", "edit", "reject"]},
                "send_wechat_message_to_contact": {
                    "allowed_decisions": ["approve", "edit", "reject"],
                    "description": "向微信联系人发送消息需要您确认",
                },
            },
            description_prefix="工具执行需要审批",
        )
    )
    #4.修复模型输出的非法 JSON 工具参数；必须在 HITL 内层，保证修复后的调用仍被审批
    middleware.append(ToolCallRepairMiddleware())
    return middleware


def build_agent(checkpointer=None, *, store=None):
    """装配主 agent

    Args:
        checkpointer: 短期记忆 checkpointer（AsyncPostgresSaver / InMemorySaver）
        store: 长期记忆 store（AsyncPostgresStore）；None 时仅显式偏好

    Returns:
        CompiledStateGraph
    """
    return create_agent(
        model=get_llm_strong(),
        tools=[
            ingest_wechat_group_messages, ingest_text_messages, query_jobs,
            select_offers, add_to_offers, search_resume, export_job_report,
            get_today_weather, send_wechat_message_to_contact,
        ],
        middleware=build_middleware(),
        system_prompt=build_system_prompt(),
        checkpointer=checkpointer,
        store=store,
    )