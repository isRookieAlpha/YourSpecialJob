# -*- coding: utf-8 -*-
"""跟踪 DAO 测试：指纹账本 / 组状态 / 推送日志（真实测试库）。"""
from datetime import date, datetime, timedelta

from agent.db.tracking_dao import (
    cleanup_ledger,
    cleanup_push_log,
    ensure_group_state,
    filter_known_content,
    filter_unpushed,
    filter_unseen,
    get_group_state,
    list_group_states,
    list_retry_job_ids,
    mark_group_initialized,
    message_fingerprint,
    push_stats_since,
    record_push,
    record_seen,
    touch_group_state,
)

DAY = date(2026, 11, 5)
GROUP = "兼职1群"


async def insert_job(pool, summary="传菜", hash_suffix="a") -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO jobs (group_name, source, source_ref, raw_text, content_hash, work_summary)
               VALUES (%s, 'wechat_auto', 'src.json', %s, %s, %s) RETURNING id""",
            (GROUP, f"原文{summary}{hash_suffix}", f"hash-{summary}-{hash_suffix}", summary),
        )
        return (await cur.fetchone())[0]


class TestFingerprint:
    def test_stable(self):
        a = message_fingerprint(GROUP, DAY, "张姐", "10:15", "酒店传菜日结110")
        b = message_fingerprint(GROUP, DAY, "张姐", "10:15", "酒店传菜日结110")
        assert a == b and len(a) == 32

    def test_whitespace_normalized(self):
        a = message_fingerprint(GROUP, DAY, "张姐", "10:15", "酒店 传菜\n日结110")
        b = message_fingerprint(GROUP, DAY, "张姐", "10:15", "酒店 传菜 日结110")
        assert a == b

    def test_day_differs(self):
        a = message_fingerprint(GROUP, DAY, "张姐", "10:15", "文本")
        b = message_fingerprint(GROUP, date(2026, 11, 6), "张姐", "10:15", "文本")
        assert a != b


class TestLedger:
    async def test_filter_and_record(self, pool):
        fp1 = message_fingerprint(GROUP, DAY, "张姐", "10:15", "消息一")
        fp2 = message_fingerprint(GROUP, DAY, "李哥", "10:20", "消息二")

        assert await filter_unseen(pool, GROUP, DAY, [fp1, fp2]) == {fp1, fp2}
        await record_seen(pool, GROUP, DAY, [
            {"fingerprint": fp1, "sender": "张姐", "msg_time": "10:15"},
        ])
        assert await filter_unseen(pool, GROUP, DAY, [fp1, fp2]) == {fp2}

    async def test_record_is_idempotent(self, pool):
        fp = message_fingerprint(GROUP, DAY, "张姐", "10:15", "消息")
        items = [{"fingerprint": fp, "sender": "张姐", "msg_time": "10:15"}]
        await record_seen(pool, GROUP, DAY, items)
        await record_seen(pool, GROUP, DAY, items)  # 不报错
        assert await filter_unseen(pool, GROUP, DAY, [fp]) == set()

    async def test_known_content(self, pool):
        await insert_job(pool, "传菜", "h1")
        known = await filter_known_content(pool, GROUP, ["hash-传菜-h1", "不存在"])
        assert known == {"hash-传菜-h1"}

    async def test_cleanup(self, pool):
        old_day = date.today() - timedelta(days=30)
        fp = message_fingerprint(GROUP, old_day, "x", "10:00", "旧消息")
        await record_seen(pool, GROUP, old_day, [{"fingerprint": fp, "sender": "x", "msg_time": "10:00"}])
        removed = await cleanup_ledger(pool, keep_days=7)
        assert removed == 1


class TestGroupState:
    async def test_lifecycle(self, pool):
        assert await get_group_state(pool, GROUP) is None
        await ensure_group_state(pool, GROUP)
        state = await get_group_state(pool, GROUP)
        assert state["initialized_at"] is None

        await mark_group_initialized(pool, GROUP)
        state = await get_group_state(pool, GROUP)
        assert state["initialized_at"] is not None

        await touch_group_state(pool, GROUP, error="读取失败")
        state = await get_group_state(pool, GROUP)
        assert state["last_error"] == "读取失败"

        states = await list_group_states(pool)
        assert [s["group_name"] for s in states] == [GROUP]


class TestPushLog:
    async def test_record_and_filter(self, pool):
        j1 = await insert_job(pool, "传菜", "p1")
        j2 = await insert_job(pool, "布展", "p2")
        assert await filter_unpushed(pool, [j1, j2]) == {j1, j2}

        await record_push(pool, [j1], "sent", batch_id="b1", preview="摘要")
        assert await filter_unpushed(pool, [j1, j2]) == {j2}

    async def test_failed_retry_limit(self, pool):
        j1 = await insert_job(pool, "传菜", "r1")
        for _ in range(3):
            await record_push(pool, [j1], "failed", error="timeout", increment_attempt=True)
        assert await filter_unpushed(pool, [j1]) == set()
        assert await list_retry_job_ids(pool) == []

    async def test_failed_under_limit_is_retryable(self, pool):
        j1 = await insert_job(pool, "传菜", "q1")
        await record_push(pool, [j1], "failed", error="timeout", increment_attempt=True)
        assert await filter_unpushed(pool, [j1]) == {j1}
        assert await list_retry_job_ids(pool) == [j1]

    async def test_stats_and_cleanup(self, pool):
        j1 = await insert_job(pool, "传菜", "s1")
        j2 = await insert_job(pool, "布展", "s2")
        await record_push(pool, [j1], "sent")
        await record_push(pool, [j2], "skipped")
        stats = await push_stats_since(pool)
        assert stats["sent"] == 1 and stats["skipped"] == 1

        removed = await cleanup_push_log(pool, keep_days=0)
        assert removed == 2