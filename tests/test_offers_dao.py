# -*- coding: utf-8 -*-
"""心动的offer DAO 测试（真实测试库）。"""
from datetime import date

import pytest

from agent.db.offers_dao import (
    create_manual_offer,
    delete_offer,
    get_offers_by_ids,
    list_offers,
    set_offer_status,
    update_offer,
)
from agent.models.offer import ManualOfferInput, OfferUpdateInput


def manual_input(**kwargs) -> ManualOfferInput:
    base = {
        "work_summary": "酒店传菜",
        "work_date": date(2026, 11, 5),
        "work_time": "7:30-21:00",
        "commission_min": 110,
        "commission_max": 110,
        "commission_unit": "元/天",
        "location": "静安寺",
    }
    base.update(kwargs)
    return ManualOfferInput(**base)


async def insert_wechat_job(pool, summary="仓库搬货", offer_status="none") -> int:
    """插入一条模拟微信群来源记录，返回 id"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO jobs (group_name, source, source_ref, raw_text, content_hash,
                                 work_summary, work_date, work_time,
                                 commission_min, commission_max, commission_unit,
                                 location, offer_status)
               VALUES ('兼职1群', 'wechat_auto', 'src.json', %s, %s,
                       %s, '2026-11-06', '9:00-18:00', 180, 220, '元/天', '松江', %s)
               RETURNING id""",
            (f"原始消息{summary}", f"hash-{summary}-{offer_status}", summary, offer_status),
        )
        return (await cur.fetchone())[0]


class TestCreateManualOffer:
    async def test_create_defaults(self, pool):
        row = await create_manual_offer(pool, manual_input())
        assert row["source"] == "manual_form"
        assert row["group_name"] == "手动录入"
        assert row["offer_status"] == "favorite"
        assert "酒店传菜" in row["raw_text"]
        assert row["source_label"].startswith("手动录入")
        assert row["work_start"] == "07:30"
        assert row["work_hours"] == 13.5
        assert row["estimated_income"] == 110.0

    async def test_create_is_todo(self, pool):
        row = await create_manual_offer(pool, manual_input(is_todo=True))
        assert row["offer_status"] == "todo"

    async def test_hash_unique_across_creates(self, pool):
        a = await create_manual_offer(pool, manual_input())
        b = await create_manual_offer(pool, manual_input())
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, content_hash FROM jobs WHERE id = ANY(%s) ORDER BY id", ([a["id"], b["id"]],)
            )
            hashes = dict((r[0], r[1]) for r in await cur.fetchall())
        assert hashes[a["id"]] != hashes[b["id"]]


class TestListOffers:
    async def test_filter_and_order(self, pool):
        first = await create_manual_offer(pool, manual_input())  # favorite
        second = await create_manual_offer(pool, manual_input(work_summary="布展", is_todo=True))
        await insert_wechat_job(pool, offer_status="none")  # 不应出现

        rows = await list_offers(pool)
        assert len(rows) == 2
        # 按 id 插入顺序稳定排序（不因 todo 分组/更新时间变化）
        assert [r["id"] for r in rows] == [first["id"], second["id"]]

    async def test_edit_and_status_change_keep_position(self, pool):
        """编辑字段或升级 ToDo 后，记录在列表中的位置不变"""
        a = await create_manual_offer(pool, manual_input())
        b = await create_manual_offer(pool, manual_input(work_summary="布展"))

        await update_offer(pool, a["id"], OfferUpdateInput(work_summary="改名了", location="新地点"))
        await set_offer_status(pool, [a["id"]], "todo", mode="set")

        rows = await list_offers(pool)
        assert [r["id"] for r in rows] == [a["id"], b["id"]]

    async def test_filter_single_status(self, pool):
        await create_manual_offer(pool, manual_input())
        await create_manual_offer(pool, manual_input(is_todo=True))
        rows = await list_offers(pool, statuses=("favorite",))
        assert len(rows) == 1
        assert rows[0]["offer_status"] == "favorite"


class TestSetOfferStatus:
    async def test_set_upgrade_and_downgrade(self, pool):
        job_id = await insert_wechat_job(pool)
        updated, skipped = await set_offer_status(pool, [job_id], "favorite", mode="set")
        assert len(updated) == 1 and not skipped
        updated, _ = await set_offer_status(pool, [job_id], "todo", mode="set")
        assert updated[0]["offer_status"] == "todo"
        updated, _ = await set_offer_status(pool, [job_id], "favorite", mode="set")
        assert updated[0]["offer_status"] == "favorite"

    async def test_promote_does_not_downgrade_todo(self, pool):
        job_id = await insert_wechat_job(pool, offer_status="todo")
        updated, skipped = await set_offer_status(pool, [job_id], "favorite", mode="promote")
        assert updated == []
        assert skipped == [{"id": job_id, "offer_status": "todo"}]

    async def test_promote_todo_from_favorite(self, pool):
        job_id = await insert_wechat_job(pool, offer_status="favorite")
        updated, skipped = await set_offer_status(pool, [job_id], "todo", mode="promote")
        assert len(updated) == 1 and not skipped


class TestUpdateOffer:
    async def test_update_fields(self, pool):
        row = await create_manual_offer(pool, manual_input())
        updated = await update_offer(pool, row["id"], OfferUpdateInput(
            work_summary="酒店宴会传菜", location="人民广场", commission_min=120, commission_max=140,
        ))
        assert updated["work_summary"] == "酒店宴会传菜"
        assert updated["location"] == "人民广场"
        assert updated["commission_min"] == 120.0

    async def test_update_missing_returns_none(self, pool):
        assert await update_offer(pool, 999999, OfferUpdateInput(work_summary="x")) is None


class TestDeleteOffer:
    async def test_manual_form_hard_delete(self, pool):
        row = await create_manual_offer(pool, manual_input())
        removed, mode = await delete_offer(pool, row["id"])
        assert (removed, mode) == (True, "hard")
        assert await get_offers_by_ids(pool, [row["id"]]) == []

    async def test_wechat_source_soft_delete(self, pool):
        job_id = await insert_wechat_job(pool, offer_status="favorite")
        removed, mode = await delete_offer(pool, job_id)
        assert (removed, mode) == (True, "soft")
        rows = await get_offers_by_ids(pool, [job_id])
        assert len(rows) == 1 and rows[0]["offer_status"] == "none"

    async def test_delete_missing(self, pool):
        assert await delete_offer(pool, 999999) == (False, "")