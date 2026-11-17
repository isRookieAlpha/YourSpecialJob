# -*- coding: utf-8 -*-
"""sessions_dao 测试：标题生成 / 会话 CRUD / 首条消息自动建会话。"""
import asyncio

from agent.db.sessions_dao import (
    create_session,
    delete_session,
    list_sessions,
    make_session_title,
    upsert_session_on_message,
)


def test_make_title_truncates_to_20_chars():
    """超长标题截断 20 字"""
    assert make_session_title("一二三四五六七八九十一二三四五六七八九十多余") == "一二三四五六七八九十一二三四五六七八九十"


def test_make_title_collapses_whitespace():
    """空白折叠为单空格"""
    assert make_session_title("  帮我\n找  兼职  ") == "帮我 找 兼职"


def test_make_title_empty_returns_default():
    """空消息返回默认标题"""
    assert make_session_title("   ") == "新会话"


async def test_create_list_delete(pool):
    """显式创建 → 列表包含 → 删除"""
    created = await create_session(pool, "t-1", "测试会话")
    assert created["title"] == "测试会话"

    sessions = await list_sessions(pool)
    assert [s["id"] for s in sessions] == ["t-1"]

    assert await delete_session(pool, "t-1") is True
    assert await list_sessions(pool) == []
    assert await delete_session(pool, "t-1") is False


async def test_upsert_sets_title_on_first_message_only(pool):
    """首条消息设标题；后续仅刷新 updated_at 不改标题"""
    await upsert_session_on_message(pool, "t-2", "读取兼职1群26/11/3的消息")
    first = (await list_sessions(pool))[0]
    assert first["title"] == "读取兼职1群26/11/3的消息"

    await asyncio.sleep(0.01)
    await upsert_session_on_message(pool, "t-2", "帮我推荐佣金高的")
    second = (await list_sessions(pool))[0]
    assert second["title"] == first["title"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["id"] == "t-2"


async def test_upsert_replaces_default_title(pool):
    """显式创建的"新会话"占位标题会被首条消息替换"""
    await create_session(pool, "t-3")
    await upsert_session_on_message(pool, "t-3", "第一次提问内容")
    assert (await list_sessions(pool))[0]["title"] == "第一次提问内容"