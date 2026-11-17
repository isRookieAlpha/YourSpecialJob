# -*- coding: utf-8 -*-
"""通勤入库服务测试（mock MCP，真实测试库）。"""
from types import SimpleNamespace

import pytest

import agent.commute_service as svc
from agent.amap_mcp import AmapMcpError


async def insert_job(pool, location: str | None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO jobs (group_name, source, source_ref, raw_text, content_hash, location)
               VALUES ('兼职1群', 'wechat_auto', 'src.json', '原文', %s, %s) RETURNING id""",
            (f"hash-{location}", location),
        )
        return (await cur.fetchone())[0]


def patch_prefs(monkeypatch, prefs):
    monkeypatch.setattr(
        svc, "get_settings", lambda: SimpleNamespace(preferences=prefs)
    )


class TestCommuteService:
    async def test_no_origin_skips(self, pool, monkeypatch):
        patch_prefs(monkeypatch, {})
        jid = await insert_job(pool, "静安寺")
        ok, mcp_failed = await svc.annotate_and_persist_commute(pool, [{"id": jid, "location": "静安寺"}])
        assert (ok, mcp_failed) == (0, False)

    async def test_no_location_skips(self, pool, monkeypatch):
        patch_prefs(monkeypatch, {"commute_origin": "人民广场", "amap_city": "上海"})
        jid = await insert_job(pool, None)
        ok, _ = await svc.annotate_and_persist_commute(pool, [{"id": jid, "location": None}])
        assert ok == 0

    async def test_success_updates_db(self, pool, monkeypatch):
        patch_prefs(monkeypatch, {"commute_origin": "人民广场", "amap_city": "上海"})
        jid = await insert_job(pool, "静安寺")

        async def fake_compute(rows, origin, city):
            return {jid: {"distance_m": 3200, "bike_min": 14, "transit_min": 25, "drive_min": 18}}

        monkeypatch.setattr(svc, "compute_commute_batch", fake_compute)
        ok, mcp_failed = await svc.annotate_and_persist_commute(
            pool, [{"id": jid, "location": "静安寺"}]
        )
        assert (ok, mcp_failed) == (1, False)

        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT commute_distance_m, commute_bike_min, commute_transit_min, commute_drive_min "
                "FROM jobs WHERE id = %s",
                (jid,),
            )
            assert await cur.fetchone() == (3200, 14, 25, 18)

    async def test_mcp_unavailable_flag(self, pool, monkeypatch):
        patch_prefs(monkeypatch, {"commute_origin": "人民广场"})
        jid = await insert_job(pool, "静安寺")

        async def failing(rows, origin, city):
            raise AmapMcpError("高德 MCP 调用失败")

        monkeypatch.setattr(svc, "compute_commute_batch", failing)
        ok, mcp_failed = await svc.annotate_and_persist_commute(
            pool, [{"id": jid, "location": "静安寺"}]
        )
        assert (ok, mcp_failed) == (0, True)