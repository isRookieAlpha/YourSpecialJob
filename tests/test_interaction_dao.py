# -*- coding: utf-8 -*-
"""interaction_dao 测试：中介待审批前缀查询 / 原子领取 / 过期清理 / 轮询状态。"""
from agent.db.interaction_dao import (
    CONTACT_THREAD_PREFIX,
    claim_pending,
    cleanup_contact_pending,
    count_pending_by_prefix,
    ensure_state,
    get_contact_pending,
    list_contact_pending,
    list_states,
    save_pending,
    touch_state,
)


def contact_thread(name: str) -> str:
    return f"{CONTACT_THREAD_PREFIX}{name}"


async def test_get_contact_pending_ignores_user_thread(pool):
    """用户互动 pending 不混入中介列表"""
    await save_pending(pool, "wechat:Alpha", "Alpha", {"mode": "hitl"})
    await save_pending(pool, contact_thread("小王"), "小王", {"mode": "draft", "draft": "hi"})

    pending = await get_contact_pending(pool)
    assert pending is not None
    assert pending["thread_id"] == contact_thread("小王")
    assert pending["conversation"] == "小王"
    assert pending["interrupt_payload"]["mode"] == "draft"
    assert pending["expired"] is False


async def test_contact_pending_earliest_first(pool):
    await save_pending(pool, contact_thread("小王"), "小王", {"mode": "draft"})
    await save_pending(pool, contact_thread("张姐"), "张姐", {"mode": "draft"})

    items = await list_contact_pending(pool)
    assert [p["conversation"] for p in items] == ["小王", "张姐"]


async def test_claim_pending_only_first_succeeds(pool):
    """双通道并发确认：DELETE ... RETURNING 只有一个调用者拿到"""
    thread = contact_thread("小王")
    await save_pending(pool, thread, "小王", {"mode": "draft", "draft": "hi"})

    first = await claim_pending(pool, thread)
    second = await claim_pending(pool, thread)
    assert first is not None and first["conversation"] == "小王"
    assert second is None
    assert await get_contact_pending(pool) is None


async def test_cleanup_contact_pending_only_expired_contact_rows(pool):
    """过期清理只针对中介前缀；用户 pending 保留"""
    thread = contact_thread("小王")
    await save_pending(pool, thread, "小王", {"mode": "draft"})
    await save_pending(pool, "wechat:Alpha", "Alpha", {"mode": "hitl"})
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE interaction_pending SET expires_at = now() - interval '1 minute' "
            "WHERE thread_id = %s",
            (thread,),
        )

    removed = await cleanup_contact_pending(pool)
    assert removed == [thread]
    assert await get_contact_pending(pool) is None
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM interaction_pending WHERE thread_id = 'wechat:Alpha'"
        )
        assert (await cur.fetchone())[0] == 1


async def test_count_pending_by_prefix(pool):
    await save_pending(pool, contact_thread("小王"), "小王", {"mode": "draft"})
    await save_pending(pool, "wechat:Alpha", "Alpha", {"mode": "hitl"})
    assert await count_pending_by_prefix(pool, CONTACT_THREAD_PREFIX) == 1


async def test_list_states_oldest_first(pool):
    """轮转依据：从未轮询（NULL）优先，其次最久未轮询"""
    await ensure_state(pool, "小王")
    await ensure_state(pool, "张姐")
    await touch_state(pool, "张姐")

    states = await list_states(pool, ["小王", "张姐"])
    assert [s["conversation"] for s in states] == ["小王", "张姐"]
    assert states[0]["last_poll_at"] is None
    assert states[1]["last_poll_at"] is not None