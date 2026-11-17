# -*- coding: utf-8 -*-
"""
psycopg 异步连接池单例（业务表专用，与 langgraph Saver/Store 各管各的池）。

本地 PG 为 trust 认证（无密码），DSN 直接拼 settings 中的连接信息。
FastAPI lifespan 退出时调用 close_pool()。
"""
from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.config import get_settings

#1.模块级单例（懒加载）
_pool: AsyncConnectionPool | None = None


def build_dsn() -> str:
    """由 settings 拼装 PostgreSQL DSN（本地 trust 认证，无密码）"""
    s = get_settings()
    return f"postgresql://{s.postgres_user}@{s.postgres_host}:{s.postgres_port}/{s.postgres_db}"


async def get_pool() -> AsyncConnectionPool:
    """获取连接池单例（首次调用时创建并打开，min 1 / max 5，autocommit）"""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=build_dsn(),
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True},
            open=False,  # psycopg_pool 3.2+ 默认不自动打开，显式 open 并等待就绪
        )
        await _pool.open(wait=True, timeout=10)
        logger.info("PostgreSQL 连接池已就绪: {}", build_dsn())
    return _pool


async def close_pool() -> None:
    """关闭连接池（lifespan 退出时调用；未初始化时静默）"""
    global _pool
    if _pool is not None:
        await _pool.close()
        logger.info("PostgreSQL 连接池已关闭")
        _pool = None