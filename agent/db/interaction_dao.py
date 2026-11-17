# -*- coding: utf-8 -*-
"""
微信互动数据访问层：会话轮询状态 + HITL 待审批（跨轮 resume）。
"""
from datetime import datetime, timedelta

from loguru import logger
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

# 中介待审批的 thread 前缀（与用户互动会话 wechat:{target} 区分）
CONTACT_THREAD_PREFIX = "wechat:contact:"


def _pending_row(row) -> dict:
    """行 → pending 字典（expired 由 expires_at 与当前时间比较得出）"""
    expires_at = row[4]
    return {
        "thread_id": row[0],
        "conversation": row[1],
        "interrupt_payload": dict(row[2]) if row[2] else {},
        "created_at": row[3].isoformat() if row[3] else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "expired": bool(expires_at and expires_at < datetime.now().astimezone()),
    }


async def get_state(pool: AsyncConnectionPool, conversation: str) -> dict | None:
    """读取互动会话状态（不存在返回 None）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT conversation, initialized_at, last_poll_at, last_reply_at, last_error "
            "FROM interaction_state WHERE conversation = %s",
            (conversation,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "conversation": row[0],
        "initialized_at": row[1].isoformat() if row[1] else None,
        "last_poll_at": row[2].isoformat() if row[2] else None,
        "last_reply_at": row[3].isoformat() if row[3] else None,
        "last_error": row[4],
    }


async def ensure_state(pool: AsyncConnectionPool, conversation: str) -> None:
    """确保状态行存在（幂等）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO interaction_state (conversation) VALUES (%s) "
            "ON CONFLICT (conversation) DO NOTHING",
            (conversation,),
        )


async def mark_initialized(pool: AsyncConnectionPool, conversation: str) -> None:
    """标记首轮基线完成（历史消息只记录不回复）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO interaction_state (conversation, initialized_at, last_poll_at) "
            "VALUES (%s, now(), now()) "
            "ON CONFLICT (conversation) DO UPDATE SET initialized_at = now(), last_poll_at = now()",
            (conversation,),
        )


async def touch_state(
    pool: AsyncConnectionPool,
    conversation: str,
    error: str | None = None,
    replied: bool = False,
) -> None:
    """更新轮询时间/错误；replied=True 时刷新 last_reply_at（发送冷却）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO interaction_state (conversation, last_poll_at, last_reply_at, last_error) "
            "VALUES (%s, now(), CASE WHEN %s THEN now() ELSE NULL END, %s) "
            "ON CONFLICT (conversation) DO UPDATE SET "
            "last_poll_at = now(), "
            "last_reply_at = CASE WHEN %s THEN now() ELSE interaction_state.last_reply_at END, "
            "last_error = EXCLUDED.last_error",
            (conversation, replied, error, replied),
        )


async def save_pending(
    pool: AsyncConnectionPool,
    thread_id: str,
    conversation: str,
    payload: dict,
    ttl_min: int = 1440,
) -> None:
    """保存待审批操作（覆盖旧记录）"""
    expires_at = datetime.now().astimezone() + timedelta(minutes=ttl_min)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO interaction_pending (thread_id, conversation, interrupt_payload, expires_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (thread_id) DO UPDATE SET "
            "conversation = EXCLUDED.conversation, interrupt_payload = EXCLUDED.interrupt_payload, "
            "created_at = now(), expires_at = EXCLUDED.expires_at",
            (thread_id, conversation, Jsonb(payload), expires_at),
        )
    logger.info("互动待审批已保存 thread={} 过期时间={}", thread_id, expires_at.isoformat())


async def get_pending(pool: AsyncConnectionPool, thread_id: str) -> dict | None:
    """读取待审批记录（过期自动删除并返回 None）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT thread_id, conversation, interrupt_payload, created_at, expires_at "
            "FROM interaction_pending WHERE thread_id = %s",
            (thread_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        expires_at = row[4]
        if expires_at is not None and expires_at < datetime.now().astimezone():
            await conn.execute("DELETE FROM interaction_pending WHERE thread_id = %s", (thread_id,))
            logger.info("互动待审批已过期并清理 thread={}", thread_id)
            return None
    return {
        "thread_id": row[0],
        "conversation": row[1],
        "interrupt_payload": dict(row[2]) if row[2] else {},
        "created_at": row[3].isoformat() if row[3] else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


async def clear_pending(pool: AsyncConnectionPool, thread_id: str) -> None:
    """清除待审批记录"""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM interaction_pending WHERE thread_id = %s", (thread_id,))


async def cleanup_expired(pool: AsyncConnectionPool) -> int:
    """清理全部过期待审批记录（每轮最多执行一次）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM interaction_pending WHERE expires_at < now() RETURNING thread_id"
        )
        removed = len(await cur.fetchall())
    if removed:
        logger.info("已清理 {} 条过期互动待审批", removed)
    return removed


# ---------- 中介待审批（thread_id 前缀 wechat:contact:） ----------


async def list_contact_pending(pool: AsyncConnectionPool) -> list[dict]:
    """列出有效的中介待审批（按创建时间升序，最早一条优先处理）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT thread_id, conversation, interrupt_payload, created_at, expires_at "
            "FROM interaction_pending WHERE thread_id LIKE %s AND expires_at >= now() "
            "ORDER BY created_at ASC",
            (f"{CONTACT_THREAD_PREFIX}%",),
        )
        return [_pending_row(r) for r in await cur.fetchall()]


async def get_contact_pending(pool: AsyncConnectionPool) -> dict | None:
    """取最早的有效中介待审批（无则返回 None）"""
    items = await list_contact_pending(pool)
    return items[0] if items else None


async def cleanup_contact_pending(pool: AsyncConnectionPool) -> list[str]:
    """删除过期的中介待审批，返回 thread_id 列表（调用方负责清理 checkpoint）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM interaction_pending WHERE thread_id LIKE %s AND expires_at < now() "
            "RETURNING thread_id",
            (f"{CONTACT_THREAD_PREFIX}%",),
        )
        ids = [r[0] for r in await cur.fetchall()]
    if ids:
        logger.info("已清理 {} 条过期中介待审批", len(ids))
    return ids


async def claim_pending(pool: AsyncConnectionPool, thread_id: str) -> dict | None:
    """原子领取待审批（DELETE ... RETURNING）：双通道并发时只有一个调用者拿到"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM interaction_pending WHERE thread_id = %s "
            "RETURNING thread_id, conversation, interrupt_payload, created_at, expires_at",
            (thread_id,),
        )
        row = await cur.fetchone()
    return _pending_row(row) if row else None


async def count_pending_by_prefix(pool: AsyncConnectionPool, prefix: str) -> int:
    """统计某前缀的待审批数量（状态接口用）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM interaction_pending WHERE thread_id LIKE %s",
            (f"{prefix}%",),
        )
        return (await cur.fetchone())[0]


async def list_states(
    pool: AsyncConnectionPool, conversations: list[str]
) -> list[dict]:
    """列出指定会话的轮询状态（轮转选择与状态接口用）"""
    if not conversations:
        return []
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT conversation, initialized_at, last_poll_at, last_reply_at, last_error "
            "FROM interaction_state WHERE conversation = ANY(%s) "
            "ORDER BY last_poll_at NULLS FIRST",
            (list(conversations),),
        )
        rows = await cur.fetchall()
    return [
        {
            "conversation": r[0],
            "initialized_at": r[1].isoformat() if r[1] else None,
            "last_poll_at": r[2].isoformat() if r[2] else None,
            "last_reply_at": r[3].isoformat() if r[3] else None,
            "last_error": r[4],
        }
        for r in rows
    ]