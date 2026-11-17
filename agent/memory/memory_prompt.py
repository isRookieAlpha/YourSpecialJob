# -*- coding: utf-8 -*-
"""
长期记忆动态提示词中间件：每次模型调用前组装 system prompt。

- 显式偏好（设置页 preferences）：每次实时读取，设置保存后无需重建 agent
- 固定 profile 记忆：store.aget(namespace, "profile")，无 embedding 成本
- 长尾记忆：仅当"最后一条消息是用户消息"且 store 配置了向量索引时
  asearch(top4)，避免每次工具循环都做 embedding
- 任何异常降级为仅显式偏好（不能阻塞聊天）
"""
from langchain.agents.middleware import dynamic_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from agent.config import get_settings
from agent.memory.preference_extractor import NAMESPACE
from agent.preferences import load_chat_preferences
from agent.prompts.system import build_system_prompt

MAX_MEMORY_CHARS = 1500
MAX_MEMORY_ITEMS = 6


async def collect_memories(store, messages: list) -> list[str]:
    """从 store 收集记忆（固定 profile + 首条用户消息的语义检索）"""
    memories: list[str] = []
    if store is None:
        return memories

    #1.固定 profile（低成本）
    profile = await store.aget(NAMESPACE, "profile")
    if profile is not None:
        text = (profile.value or {}).get("text")
        if text:
            memories.append(str(text))

    #2.仅用户新消息时做语义检索（避免工具循环重复 embedding）
    last = messages[-1] if messages else None
    if isinstance(last, HumanMessage) and getattr(store, "index_config", None):
        hits = await store.asearch(NAMESPACE, query=str(last.content or "")[:500], limit=4)
        for hit in hits:
            text = (hit.value or {}).get("text")
            if text and text not in memories:
                memories.append(str(text))
    return memories


def trim_memories(memories: list[str]) -> list[str]:
    """截断记忆块长度，避免挤占上下文"""
    trimmed: list[str] = []
    total = 0
    for item in memories[:MAX_MEMORY_ITEMS]:
        if total + len(item) > MAX_MEMORY_CHARS:
            break
        trimmed.append(item)
        total += len(item)
    return trimmed


@dynamic_prompt
async def memory_prompt(request) -> SystemMessage:
    """动态生成 system prompt（显式偏好 + 长期记忆 + 冲突规则）"""
    prefs = load_chat_preferences(get_settings().preferences)
    try:
        memories = await collect_memories(
            getattr(request.runtime, "store", None),
            request.state.get("messages") or [],
        )
    except Exception:
        logger.opt(exception=True).warning("长期记忆读取失败，降级为仅显式偏好")
        memories = []

    return SystemMessage(content=build_system_prompt(prefs=prefs, memories=trim_memories(memories)))