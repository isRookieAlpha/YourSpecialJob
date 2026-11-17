# -*- coding: utf-8 -*-
"""
强/弱/备用三档模型构造（lru_cache 单例，设置页变更后 reset_models 重建）。

统一走 init_chat_model + OpenAI 兼容协议（api_key + base_url 来自 agent/.env）。
强模型加 profile.max_input_tokens —— SummarizationMiddleware 的 fraction trigger
依赖模型 profile 中的上下文窗口大小，缺失时 80% 阈值无法计算（学习项目验证的坑）。
"""
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent.config import get_settings
from agent.models.job import ExtractedBatch


@lru_cache
def get_llm_strong() -> BaseChatModel:
    """强模型：主 agent 推理与推荐排序"""
    s = get_settings()
    return init_chat_model(
        model=s.strong_model,
        model_provider="openai",
        api_key=s.strong_api_key,
        base_url=s.strong_base_url,
        temperature=0.3,
        profile={"max_input_tokens": 128_000},  # Summarization fraction trigger 生效前提
    )


@lru_cache
def get_llm_weak() -> BaseChatModel:
    """弱模型：结构化提取 / 偏好提取（temperature=0 保证输出稳定）"""
    s = get_settings()
    return init_chat_model(
        model=s.weak_model,
        model_provider="openai",
        api_key=s.weak_api_key,
        base_url=s.weak_base_url,
        temperature=0,
        profile={"max_input_tokens": 128_000},
    )


@lru_cache
def get_llm_backup() -> BaseChatModel | None:
    """备用模型：主模型重试失败后 fallback；key 未配置时返回 None"""
    s = get_settings()
    if not (s.backup_model and s.backup_api_key and s.backup_base_url):
        return None
    return init_chat_model(
        model=s.backup_model,
        model_provider="openai",
        api_key=s.backup_api_key,
        base_url=s.backup_base_url,
        temperature=0.3,
    )


@lru_cache
def get_extractor():
    """结构化提取链：弱模型 + ExtractedBatch schema（懒加载避免 import 期报错）"""
    return get_llm_weak().with_structured_output(ExtractedBatch)


@lru_cache
def get_embeddings():
    """embedding 构造：OpenAI 兼容接口（M4 简历 RAG / M6 长期记忆索引使用）"""
    from langchain_openai import OpenAIEmbeddings

    s = get_settings()
    return OpenAIEmbeddings(
        model=s.embedding_model,
        api_key=s.embedding_api_key,
        base_url=s.embedding_base_url,
        # DashScope 等兼容端点不接受 tiktoken 的 token 数组，必须直接发送原文字符串
        check_embedding_ctx_length=False,
    )


@lru_cache
def get_memory_extractor():
    """长期记忆提取链：弱模型 + MemoryBatch schema（M6）"""
    from agent.models.memory import MemoryBatch

    return get_llm_weak().with_structured_output(MemoryBatch)


def is_model_configured(kind: str) -> bool:
    """检查某档模型配置是否齐全（health 检查用，不调 LLM）

    Args:
        kind: strong / weak / backup / embedding
    """
    s = get_settings()
    model = getattr(s, f"{kind if kind != 'embedding' else 'embedding'}_model", "")
    key = getattr(s, f"{kind}_api_key", "")
    url = getattr(s, f"{kind}_base_url", "")
    return bool(model and key and url)


def reset_models() -> None:
    """清空模型缓存（M3 设置页动态更新时调用；Settings 缓存由调用方另行清理）"""
    get_llm_strong.cache_clear()
    get_llm_weak.cache_clear()
    get_llm_backup.cache_clear()
    get_extractor.cache_clear()
    get_embeddings.cache_clear()
    get_memory_extractor.cache_clear()