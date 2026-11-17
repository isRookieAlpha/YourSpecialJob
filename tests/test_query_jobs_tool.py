# -*- coding: utf-8 -*-
"""query_jobs 工具测试：来源文案 / offers 载荷 / writer 异常降级（不碰真实库）。"""
from datetime import datetime, timezone, timedelta

import agent.tools.query_jobs as tool_mod
from agent.tools.query_jobs import _format_rows, _public_offer_row, query_jobs

CST = timezone(timedelta(hours=8))


def sample_row(**kwargs) -> dict:
    base = {
        "id": 12,
        "group_name": "兼职1群",
        "source": "wechat_auto",
        "source_ref": "src.json",
        "work_summary": "酒店传菜",
        "work_date": "2026-11-05",
        "work_time": "7:30-21:00",
        "commission_min": 110.0,
        "commission_max": 110.0,
        "commission_unit": "元/天",
        "commission_text": "日结110",
        "location": "静安寺",
        "physical_intensity": "轻",
        "contact": "微信abc",
        "publisher": "张姐",
        "notes": None,
        "raw_text": "很长的原文" * 50,
        "publish_time": datetime(2026, 11, 3, 10, 15, tzinfo=CST),
        "ingested_at": datetime(2026, 11, 4, 20, 15, tzinfo=CST),
        "offer_status": "none",
    }
    base.update(kwargs)
    return base


class TestFormatRows:
    def test_source_and_time_included(self):
        text = _format_rows([sample_row()])
        assert "来源=微信群「兼职1群」自动读取" in text
        assert "采集于 11-04 20:15" in text
        assert "消息发布 11-03 10:15" in text

    def test_status_hint(self):
        text = _format_rows([sample_row(offer_status="todo")])
        assert "状态=已是ToDo待办" in text
        text = _format_rows([sample_row(offer_status="favorite")])
        assert "状态=已是心动的offer" in text
        text = _format_rows([sample_row(offer_status="none")])
        assert "状态=" not in text

    def test_empty(self):
        assert "未找到" in _format_rows([])

    def test_publisher_included(self):
        text = _format_rows([sample_row(publisher="张姐")])
        assert "发布者=张姐" in text


class TestPublicOfferRow:
    def test_excludes_raw_text_and_adds_label(self):
        item = _public_offer_row(sample_row())
        assert "raw_text" not in item
        assert "source_label" in item
        assert item["id"] == 12
        assert item["offer_status"] == "none"
        assert item["commission_min"] == 110.0
        assert item["publisher"] == "张姐"


class TestSelectOffers:
    async def test_replace_event_and_order(self, monkeypatch):
        """select_offers 覆盖候选池：按传入顺序、过滤已收藏/ToDo"""
        from agent.tools.query_jobs import select_offers

        rows = [sample_row(id=1), sample_row(id=2, offer_status="todo")]
        events: list[dict] = []

        async def fake_get_jobs(pool, ids):
            return [r for r in rows if r["id"] in ids]

        async def fake_pool():
            return object()

        def fake_writer():
            return lambda payload: events.append(payload)

        monkeypatch.setattr(tool_mod, "get_jobs_by_ids", fake_get_jobs)
        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "get_stream_writer", fake_writer)

        text = await select_offers.ainvoke({"job_ids": [2, 1]})
        assert events[0]["type"] == "offers"
        assert events[0]["replace"] is True
        assert [item["id"] for item in events[0]["items"]] == [1]
        assert "已记录本轮推荐（1 条" in text
        assert "1. 酒店传菜（id=1）" in text

    async def test_empty_ids(self, monkeypatch):
        from agent.tools.query_jobs import select_offers

        text = await select_offers.ainvoke({"job_ids": []})
        assert "未提供兼职 id" in text

    async def test_returns_numbered_list(self, monkeypatch):
        """返回编号清单（微信端可回复"添加第 N 条"）"""
        from agent.tools.query_jobs import select_offers

        rows = [sample_row(id=1), sample_row(id=2)]
        events: list[dict] = []

        async def fake_get_jobs(pool, ids):
            return [r for r in rows if r["id"] in ids]

        async def fake_pool():
            return object()

        def fake_writer():
            return lambda payload: events.append(payload)

        monkeypatch.setattr(tool_mod, "get_jobs_by_ids", fake_get_jobs)
        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "get_stream_writer", fake_writer)

        text = await select_offers.ainvoke({"job_ids": [1, 2]})
        assert "已记录本轮推荐（2 条" in text
        assert "1. 酒店传菜（id=1）" in text
        assert "添加第 N 条" in text


class TestAddToOffers:
    async def test_promotes_and_reports(self, monkeypatch):
        """加入心动的offer：promote 模式，已在收藏/ToDo 的跳过"""
        from agent.tools.query_jobs import add_to_offers

        updated = [{"id": 1, "work_summary": "传菜", "offer_status": "favorite"}]
        skipped = [{"id": 2, "offer_status": "todo"}]

        async def fake_set(pool, ids, status, mode="set"):
            assert status == "favorite"
            assert mode == "promote"
            return updated, skipped

        async def fake_pool():
            return object()

        monkeypatch.setattr(tool_mod, "set_offer_status", fake_set)
        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)

        text = await add_to_offers.ainvoke({"job_ids": [1, 2]})
        assert "已加入心动的offer" in text
        assert "传菜" in text
        assert "未重复添加" in text

    async def test_empty_ids(self):
        from agent.tools.query_jobs import add_to_offers

        text = await add_to_offers.ainvoke({"job_ids": []})
        assert "未提供兼职 id" in text


class TestToolWriter:
    async def test_writer_failure_does_not_break_query(self, monkeypatch):
        """writer 抛异常（非流式上下文）时查询仍返回文本"""
        async def fake_pool():
            return object()

        async def fake_query(pool, filters):
            return [sample_row()]

        def failing_writer():
            raise RuntimeError("no stream writer")

        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "dao_query", fake_query)
        monkeypatch.setattr(tool_mod, "get_stream_writer", failing_writer)

        text = await query_jobs.ainvoke({"limit": 5})
        assert "酒店传菜" in text
        assert "来源=" in text

    async def test_writer_receives_offers_event(self, monkeypatch):
        """流式上下文中 writer 收到 offers 结构化事件"""
        captured = []

        async def fake_pool():
            return object()

        async def fake_query(pool, filters):
            return [sample_row()]

        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "dao_query", fake_query)
        monkeypatch.setattr(tool_mod, "get_stream_writer", lambda: captured.append)

        await query_jobs.ainvoke({"limit": 5})
        assert captured and captured[0]["type"] == "offers"
        assert captured[0]["items"][0]["id"] == 12
        assert "raw_text" not in captured[0]["items"][0]

    async def test_group_and_source_filters_passed(self, monkeypatch):
        """group_name / source 参数透传到 DAO 过滤条件（截图批次限定）"""
        captured = {}

        async def fake_pool():
            return object()

        async def fake_query(pool, filters):
            captured["filters"] = filters
            return []

        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "dao_query", fake_query)
        monkeypatch.setattr(tool_mod, "get_stream_writer", lambda: (lambda payload: None))

        await query_jobs.ainvoke({
            "group_name": "图片上传_20261105_153012",
            "source": "manual_image",
        })
        assert captured["filters"].group_name == "图片上传_20261105_153012"
        assert captured["filters"].source == "manual_image"

    async def test_ingested_time_filters_parsed(self, monkeypatch):
        """ingested_after/before 解析为 datetime（两段式推荐的时间分界）"""
        captured = {}

        async def fake_pool():
            return object()

        async def fake_query(pool, filters):
            captured["filters"] = filters
            return []

        monkeypatch.setattr(tool_mod, "get_pool", fake_pool)
        monkeypatch.setattr(tool_mod, "dao_query", fake_query)
        monkeypatch.setattr(tool_mod, "get_stream_writer", lambda: (lambda payload: None))

        await query_jobs.ainvoke({
            "ingested_after": "2026-11-05T13:55:00+08:00",
            "ingested_before": "2026-11-05T13:55:00Z",
        })
        assert captured["filters"].ingested_after is not None
        assert captured["filters"].ingested_before is not None
        assert captured["filters"].ingested_after.utcoffset() is not None