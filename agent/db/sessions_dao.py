# -*- coding: utf-8 -*-
"""
sessions 会话元数据 DAO（标题/时间；消息历史在 langgraph checkpointer 表）。

会话 id 即 checkpointer 的 thread_id。首条消息到达时自动建会话并把标题设为
消息前 20 字，后续消息只刷新 updated_at。
"""
import re

from loguru import logger
from psycopg_pool import AsyncConnectionPool

_WS_RE = re.compile(r"\s+")

DEFAULT_TITLE = "新会话"


def make_session_title(message: str, max_len: int = 20) -> str:
    """从消息生成会话标题（空白折叠、截断 max_len 字；空消息返回默认标题）"""
    title = _WS_RE.sub(" ", message or "").strip()
    if not title:
        return DEFAULT_TITLE
    return title[:max_len]


async def get_session(pool: AsyncConnectionPool, session_id: str) -> dict | None:
    """按 id 查会话元数据（不存在返回 None）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "title": row[1],
        "created_at": row[2].isoformat(), "updated_at": row[3].isoformat(),
    }


async def list_sessions(pool: AsyncConnectionPool) -> list[dict]:
    """列出会话（按最近更新倒序）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        )
        columns = [c.name for c in cur.description]
        return [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(columns, row)}
            for row in await cur.fetchall()
        ]


async def create_session(
    pool: AsyncConnectionPool, session_id: str, title: str | None = None
) -> dict:
    """显式创建会话（已存在时仅刷新 updated_at）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO sessions (id, title) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET updated_at = now(), "
            "title = CASE WHEN sessions.title = %s AND EXCLUDED.title <> %s "
            "THEN EXCLUDED.title ELSE sessions.title END "
            "RETURNING id, title, created_at, updated_at",
            (session_id, title or DEFAULT_TITLE, DEFAULT_TITLE, DEFAULT_TITLE),
        )
        row = await cur.fetchone()
    return {
        "id": row[0], "title": row[1],
        "created_at": row[2].isoformat(), "updated_at": row[3].isoformat(),
    }


async def upsert_session_on_message(
    pool: AsyncConnectionPool, thread_id: str, message: str
) -> None:
    """首条消息自动建会话；"新会话"占位标题被首条消息替换；后续仅刷新 updated_at"""
    title = make_session_title(message)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, title) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET updated_at = now(), "
            "title = CASE WHEN sessions.title = %s AND EXCLUDED.title <> %s "
            "THEN EXCLUDED.title ELSE sessions.title END",
            (thread_id, title, DEFAULT_TITLE, DEFAULT_TITLE),
        )
    logger.debug("会话已更新 thread_id={} title={!r}", thread_id, title)


async def delete_session(pool: AsyncConnectionPool, session_id: str) -> bool:
    """删除会话元数据（返回是否删除到记录）"""
    async with pool.connection() as conn:
        cur = await conn.execute("DELETE FROM sessions WHERE id = %s RETURNING id", (session_id,))
        return (await cur.fetchone()) is not None