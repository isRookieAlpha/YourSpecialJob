# -*- coding: utf-8 -*-
"""全库通勤重算测试（mock MCP/地理编码，真实测试库）。"""
from types import SimpleNamespace

import agent.commute_refresh as refresh_mod
from agent.amap_mcp import AmapMcpError
from agent.commute_refresh import CommuteRefreshState, refresh_once


async def insert_job(pool, location: str, hash_: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO jobs (group_name, source, source_ref, raw_text, content_hash, location)
               VALUES ('兼职1群', 'wechat_auto', 'src.json', '原文', %s, %s) RETURNING id""",
            (hash_, location),
        )
        return (await cur.fetchone())[0]


def patch_settings(monkeypatch):
    monkeypatch.setattr(
        refresh_mod, "get_settings",
        lambda: SimpleNamespace(amap_server_key="test-key"),
    )


async def _ok_geo(client, address, key, city=""):
    return {"location": "121.47,31.23"}


class TestRefreshOnce:
    async def test_clears_then_writes(self, pool, monkeypatch):
        j1 = await insert_job(pool, "静安寺", "a")
        j2 = await insert_job(pool, "徐家汇", "b")
        async with pool.connection() as conn:
            await conn.execute("UPDATE jobs SET commute_bike_min = 9, commute_updated_at = now()")

        async def fake_batch(rows, origin, city, on_done=None, failures=None):
            return {
                r["id"]: {"distance_m": 1000, "bike_min": 5, "transit_min": 15, "drive_min": 10}
                for r in rows
            }

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", _ok_geo)
        monkeypatch.setattr(refresh_mod, "compute_commute_batch", fake_batch)

        state = CommuteRefreshState()
        await refresh_once(pool, state, "静安寺", "上海市")

        assert state.total == 2
        assert state.updated == 2
        assert state.failed == 0
        assert state.batches == 1
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT commute_bike_min, commute_transit_min, commute_drive_min "
                "FROM jobs WHERE id = %s", (j1,),
            )
            assert await cur.fetchone() == (5, 15, 10)

    async def test_preserves_values_when_origin_unresolvable(self, pool, monkeypatch):
        await insert_job(pool, "静安寺", "a")
        async with pool.connection() as conn:
            await conn.execute("UPDATE jobs SET commute_bike_min = 9, commute_updated_at = now()")

        async def bad_geo(client, address, key, city=""):
            return None

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", bad_geo)
        monkeypatch.setattr(
            refresh_mod, "compute_commute_batch",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")),
        )

        state = CommuteRefreshState()
        await refresh_once(pool, state, "不存在的地方", "上海市")
        assert "无法定位" in state.last_error
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT commute_bike_min FROM jobs")
            assert (await cur.fetchone())[0] == 9

    async def test_batches_by_unique_location(self, monkeypatch):
        rows = [{"id": i, "location": f"地点{i}"} for i in range(130)]
        batch_sizes: list[int] = []

        async def fake_targets(pool):
            return rows

        async def fake_clear(pool, ids):
            return len(ids)

        async def fake_persist(pool, computed):
            batch_sizes.append(len(computed))
            return len(computed)

        async def fake_batch(batch_rows, origin, city, on_done=None, failures=None):
            return {r["id"]: {"bike_min": 1} for r in batch_rows}

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", _ok_geo)
        monkeypatch.setattr(refresh_mod, "list_commute_targets", fake_targets)
        monkeypatch.setattr(refresh_mod, "clear_commute_fields", fake_clear)
        monkeypatch.setattr(refresh_mod, "persist_commute", fake_persist)
        monkeypatch.setattr(refresh_mod, "compute_commute_batch", fake_batch)

        state = CommuteRefreshState()
        await refresh_once(None, state, "静安寺", "上海市")
        assert state.batches == 3
        assert batch_sizes == [60, 60, 10]
        assert state.updated == 130

    async def test_failed_batch_keeps_old_values(self, monkeypatch):
        """批次 MCP 失败时不调用 clear（保留旧通勤数据）"""
        rows = [{"id": i, "location": f"地点{i}"} for i in range(60)]
        cleared: list[int] = []

        async def fake_targets(pool):
            return rows

        async def fake_clear(pool, ids):
            cleared.extend(ids)
            return len(ids)

        async def failing(batch_rows, origin, city, on_done=None, failures=None):
            raise AmapMcpError("boom")

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", _ok_geo)
        monkeypatch.setattr(refresh_mod, "list_commute_targets", fake_targets)
        monkeypatch.setattr(refresh_mod, "clear_commute_fields", fake_clear)
        monkeypatch.setattr(refresh_mod, "compute_commute_batch", failing)

        state = CommuteRefreshState()
        await refresh_once(None, state, "静安寺", "上海市")
        assert cleared == []
        assert state.failed == 60
        assert state.updated == 0
        assert state.failures and state.failures[0]["count"] == 60

    async def test_records_failure_details(self, monkeypatch):
        """批次部分失败：记录地点/原因/涉及兼职条数，只写回有效数据"""
        rows = [
            {"id": 1, "location": "好地址"},
            {"id": 2, "location": "坏地址"},
            {"id": 3, "location": "坏地址"},
        ]

        async def fake_targets(pool):
            return rows

        async def fake_clear(pool, ids):
            return len(ids)

        async def fake_persist(pool, computed):
            return len(computed)

        async def fake_batch(batch_rows, origin, city, on_done=None, failures=None):
            failures.append({"location": "坏地址", "reason": "地址无法识别（高德无匹配结果）"})
            return {1: {"bike_min": 5}, 2: {}, 3: {}}

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", _ok_geo)
        monkeypatch.setattr(refresh_mod, "list_commute_targets", fake_targets)
        monkeypatch.setattr(refresh_mod, "clear_commute_fields", fake_clear)
        monkeypatch.setattr(refresh_mod, "persist_commute", fake_persist)
        monkeypatch.setattr(refresh_mod, "compute_commute_batch", fake_batch)

        state = CommuteRefreshState()
        await refresh_once(None, state, "静安寺", "上海市")
        assert state.failures == [{
            "location": "坏地址",
            "reason": "地址无法识别（高德无匹配结果）",
            "count": 2,
        }]
        assert state.updated == 1
        assert state.failed == 2

    async def test_batch_failure_continues(self, monkeypatch):
        rows = [{"id": i, "location": f"地点{i}"} for i in range(70)]
        calls = {"n": 0}

        async def fake_targets(pool):
            return rows

        async def fake_clear(pool, ids):
            return len(ids)

        async def fake_persist(pool, computed):
            return len(computed)

        async def flaky_batch(batch_rows, origin, city, on_done=None, failures=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AmapMcpError("CUQPS")
            return {r["id"]: {"bike_min": 1} for r in batch_rows}

        patch_settings(monkeypatch)
        monkeypatch.setattr(refresh_mod, "geocode_info", _ok_geo)
        monkeypatch.setattr(refresh_mod, "list_commute_targets", fake_targets)
        monkeypatch.setattr(refresh_mod, "clear_commute_fields", fake_clear)
        monkeypatch.setattr(refresh_mod, "persist_commute", fake_persist)
        monkeypatch.setattr(refresh_mod, "compute_commute_batch", flaky_batch)

        state = CommuteRefreshState()
        await refresh_once(None, state, "静安寺", "上海市")
        assert state.updated == 10   # 第二批（10 个地点）
        assert state.failed == 60    # 第一批全部失败
        assert "MCP 失败" in state.last_error