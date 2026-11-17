# -*- coding: utf-8 -*-
"""依赖注入：agent / 连接池获取，以及设置变更后的 agent 重建（串行化）。"""
import asyncio

from fastapi import Request
from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.graph import build_agent
from agent.models_factory import reset_models

#1.设置变更串行化（防并发重建产生中间态）
settings_lock = asyncio.Lock()


def get_agent(request: Request):
    """当前 agent 实例（可能为 None，接口层负责降级）"""
    return request.app.state.agent


def get_pool(request: Request) -> AsyncConnectionPool:
    """业务连接池"""
    return request.app.state.pool


async def rebuild_agent(app) -> None:
    """用当前配置重建 agent 并替换 app.state.agent（失败保留旧 agent）

    Raises:
        Exception: 构建失败（调用方负责回滚配置）
    """
    async with settings_lock:
        old_agent = app.state.agent
        try:
            new_agent = build_agent(
                checkpointer=app.state.checkpointer,
                store=getattr(app.state, "store", None),
            )
        except Exception:
            logger.exception("Agent 重建失败，保留旧实例")
            app.state.agent = old_agent
            raise
        app.state.agent = new_agent
        logger.info("Agent 已按新配置重建")


def reset_all_model_caches() -> None:
    """清空模型/embedding 缓存（配合 get_settings.cache_clear 使用）"""
    reset_models()