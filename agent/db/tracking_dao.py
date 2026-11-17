# -*- coding: utf-8 -*-
"""
长期跟踪数据访问层：消息指纹账本 / 组状态 / 推送日志。

指纹 = sha256(group|day|sender|time|normalize(text))[:32]
- 含日期：跨天同一时刻同一文本不会碰撞
- 不含 index：读取顺序变化不影响稳定性
- 同发送者同一分钟的两条相同消息会折叠（与 content_hash 语义一致，可接受）
"""
import hashlib
from datetime import date, datetime, timedelta

from loguru import logger
from psycopg_pool import AsyncConnectionPool

from agent.models.job import normalize_text


def message_fingerprint(
    group_name: str, day: date, sender: str, msg_time: str, text: str
) -> str:
    """计算单条消息的稳定指纹"""
    payload = "\x1f".join([
        group_name,
        day.isoformat(),
        str(sender or "").strip(),
        str(msg_time or "").strip(),
        normalize_text(str(text or "")),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def outbound_fingerprint(conversation: str, day: date, text: str) -> str:
    """出站消息指纹（不含发送者/时间：微信回读的时间格式不可预知）"""
    payload = "\x1f".join([
        "outbound",
        str(conversation or "").strip(),
        day.isoformat(),
        normalize_text(str(text or "")),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


async def record_outbound_message(
    pool: AsyncConnectionPool, conversation: str, day: date, text: str
) -> None:
    """记录机器人发给该会话的消息（入账本，供下一轮轮询过滤自触发）"""
    body = str(text or "").strip()
    if not conversation or not body:
        return
    fingerprint = outbound_fingerprint(conversation, day, body)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO wechat_seen_messages (fingerprint, group_name, seen_date, sender, msg_time) "
            "VALUES (%s, %s, %s, 'out', '') ON CONFLICT (fingerprint) DO NOTHING",
            (fingerprint, f"outbound:{conversation}", day),
        )


async def filter_outbound(
    pool: AsyncConnectionPool, conversation: str, day: date, texts: list[str]
) -> set[str]:
    """返回命中出站账本的文本集合（轮询时过滤机器人自己发出的消息）"""
    if not texts:
        return set()
    pairs = [(outbound_fingerprint(conversation, day, t), t) for t in texts]
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT fingerprint FROM wechat_seen_messages "
            "WHERE group_name = %s AND seen_date = %s AND fingerprint = ANY(%s)",
            (f"outbound:{conversation}", day, [fp for fp, _ in pairs]),
        )
        hit = {r[0] for r in await cur.fetchall()}
    return {text for fp, text in pairs if fp in hit}


# ---------- 消息指纹账本 ----------


async def filter_unseen(
    pool: AsyncConnectionPool, group_name: str, day: date, fingerprints: list[str]
) -> set[str]:
    """返回尚未记录过的指纹集合"""
    if not fingerprints:
        return set()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT fingerprint FROM wechat_seen_messages "
            "WHERE group_name = %s AND seen_date = %s AND fingerprint = ANY(%s)",
            (group_name, day, fingerprints),
        )
        seen = {r[0] for r in await cur.fetchall()}
    return set(fingerprints) - seen


async def record_seen(
    pool: AsyncConnectionPool, group_name: str, day: date, items: list[dict]
) -> None:
    """批量写入已见指纹（items: [{fingerprint, sender, msg_time}]，幂等）"""
    if not items:
        return
    rows = [
        (it["fingerprint"], group_name, day, it.get("sender"), it.get("msg_time"))
        for it in items
    ]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO wechat_seen_messages "
                "(fingerprint, group_name, seen_date, sender, msg_time) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (fingerprint) DO NOTHING",
                rows,
            )


async def filter_known_content(
    pool: AsyncConnectionPool, group_name: str, content_hashes: list[str]
) -> set[str]:
    """返回 jobs 表中已存在的 content_hash 集合（第二层前置过滤）"""
    if not content_hashes:
        return set()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT content_hash FROM jobs WHERE group_name = %s AND content_hash = ANY(%s)",
            (group_name, content_hashes),
        )
        return {r[0] for r in await cur.fetchall()}


async def cleanup_ledger(pool: AsyncConnectionPool, keep_days: int = 7) -> int:
    """清理过期指纹（保留 keep_days 天）"""
    cutoff = date.today() - timedelta(days=keep_days)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM wechat_seen_messages WHERE seen_date < %s", (cutoff,)
        )
        return cur.rowcount


# ---------- 组状态 ----------


async def get_group_state(pool: AsyncConnectionPool, group_name: str) -> dict | None:
    """读取跟踪组状态（不存在返回 None）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT group_name, initialized_at, last_round_at, last_error "
            "FROM tracking_group_state WHERE group_name = %s",
            (group_name,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "group_name": row[0],
        "initialized_at": row[1].isoformat() if row[1] else None,
        "last_round_at": row[2].isoformat() if row[2] else None,
        "last_error": row[3],
    }


async def ensure_group_state(pool: AsyncConnectionPool, group_name: str) -> None:
    """确保组状态行存在（幂等）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO tracking_group_state (group_name) VALUES (%s) "
            "ON CONFLICT (group_name) DO NOTHING",
            (group_name,),
        )


async def mark_group_initialized(pool: AsyncConnectionPool, group_name: str) -> None:
    """标记该群完成首轮基线"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO tracking_group_state (group_name, initialized_at, last_round_at) "
            "VALUES (%s, now(), now()) "
            "ON CONFLICT (group_name) DO UPDATE SET initialized_at = now(), last_round_at = now(), last_error = NULL",
            (group_name,),
        )


async def touch_group_state(
    pool: AsyncConnectionPool, group_name: str, error: str | None = None
) -> None:
    """刷新最后运行时间与错误（None 表示本轮成功）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO tracking_group_state (group_name, last_round_at, last_error) "
            "VALUES (%s, now(), %s) "
            "ON CONFLICT (group_name) DO UPDATE SET last_round_at = now(), last_error = EXCLUDED.last_error",
            (group_name, error),
        )


async def list_group_states(pool: AsyncConnectionPool) -> list[dict]:
    """列出全部组状态（状态接口用）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT group_name, initialized_at, last_round_at, last_error "
            "FROM tracking_group_state ORDER BY group_name"
        )
        return [
            {
                "group_name": r[0],
                "initialized_at": r[1].isoformat() if r[1] else None,
                "last_round_at": r[2].isoformat() if r[2] else None,
                "last_error": r[3],
            }
            for r in await cur.fetchall()
        ]


# ---------- 推送日志 ----------


async def record_push(
    pool: AsyncConnectionPool,
    job_ids: list[int],
    status: str,
    batch_id: str | None = None,
    preview: str | None = None,
    error: str | None = None,
    increment_attempt: bool = False,
) -> None:
    """写入/更新推送记录（主键 job_id+channel upsert）"""
    if not job_ids:
        return
    # 参数顺序：job_id, status, batch_id, preview, error, status（CASE WHEN 复用 status）
    rows = [(jid, status, batch_id, preview, error, status) for jid in job_ids]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if increment_attempt:
                await cur.executemany(
                    "INSERT INTO push_log (job_id, status, attempts, batch_id, message_preview, error, "
                    "sent_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s, "
                    "CASE WHEN %s = 'sent' THEN now() ELSE NULL END, now()) "
                    "ON CONFLICT (job_id, channel) DO UPDATE SET "
                    "status = EXCLUDED.status, attempts = push_log.attempts + 1, "
                    "error = EXCLUDED.error, updated_at = now(), "
                    "sent_at = CASE WHEN EXCLUDED.status = 'sent' THEN now() ELSE push_log.sent_at END",
                    rows,
                )
            else:
                await cur.executemany(
                    "INSERT INTO push_log (job_id, status, batch_id, message_preview, error, sent_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'sent' THEN now() ELSE NULL END, now()) "
                    "ON CONFLICT (job_id, channel) DO UPDATE SET "
                    "status = EXCLUDED.status, error = EXCLUDED.error, batch_id = EXCLUDED.batch_id, "
                    "updated_at = now(), "
                    "sent_at = CASE WHEN EXCLUDED.status = 'sent' THEN now() ELSE push_log.sent_at END",
                    rows,
                )


async def filter_unpushed(pool: AsyncConnectionPool, job_ids: list[int]) -> set[int]:
    """返回未推送过（无记录，或记录为 failed 且 attempts<3）的 job id"""
    if not job_ids:
        return set()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT job_id, status, attempts FROM push_log "
            "WHERE channel = 'wechat' AND job_id = ANY(%s)",
            (job_ids,),
        )
        states = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    result = set()
    for jid in job_ids:
        state = states.get(jid)
        if state is None:
            result.add(jid)
        elif state[0] == "failed" and state[1] < 3:
            result.add(jid)
    return result


async def list_retry_job_ids(pool: AsyncConnectionPool, max_attempts: int = 3) -> list[int]:
    """列出失败且未超过重试次数的 job id（跨轮重试）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT job_id FROM push_log "
            "WHERE channel = 'wechat' AND status = 'failed' AND attempts < %s",
            (max_attempts,),
        )
        return [r[0] for r in await cur.fetchall()]


async def push_stats_since(pool: AsyncConnectionPool, hours: int = 24) -> dict:
    """近 N 小时推送统计（状态接口用）"""
    since = datetime.now().astimezone() - timedelta(hours=hours)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, count(*) FROM push_log WHERE updated_at >= %s GROUP BY status",
            (since,),
        )
        rows = await cur.fetchall()
    stats = {"sent": 0, "failed": 0, "skipped": 0}
    for status, count in rows:
        stats[status] = count
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM push_log WHERE status = 'failed' AND attempts < 3",
        )
        stats["retry_pending"] = (await cur.fetchone())[0]
    return stats


async def cleanup_push_log(pool: AsyncConnectionPool, keep_days: int = 30) -> int:
    """清理旧推送记录（failed 保留以便排查）"""
    cutoff = datetime.now().astimezone() - timedelta(days=keep_days)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM push_log WHERE updated_at < %s AND status IN ('sent', 'skipped')",
            (cutoff,),
        )
        removed = cur.rowcount
    if removed:
        logger.info("已清理 {} 条过期推送记录", removed)
    return removed