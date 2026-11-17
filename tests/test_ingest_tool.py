# -*- coding: utf-8 -*-
"""ingest_wechat_group_messages 工具测试：默认群名/日期兜底逻辑（mock 管道）。"""
from types import SimpleNamespace

import pytest

import agent.tools.ingest as tool_mod
from agent.models.job import IngestStats
from agent.tools.ingest import ingest_wechat_group_messages


def fake_stats(group: str, date_label: str) -> IngestStats:
    return IngestStats(
        group_name=group, target_date=date_label, source_ref="src.json",
        total_messages=3, noise_filtered=0, batches=1, llm_valid=1, new_inserted=1,
        dup_in_run=0, dup_jsonl=0, dup_db=0, elapsed_sec=1.0,
    )


@pytest.fixture
def patch_tool(monkeypatch):
    """替换 settings 与管道，记录实际调用参数"""
    calls = {}

    def setup(prefs: dict):
        monkeypatch.setattr(
            tool_mod, "get_settings", lambda: SimpleNamespace(preferences=prefs)
        )
        monkeypatch.setattr(tool_mod, "get_stream_writer", lambda: (lambda payload: None))

        async def fake_ingest(group_name, target_date, progress_cb=None):
            calls["args"] = (group_name, target_date)
            calls.setdefault("list", []).append((group_name, target_date))
            return fake_stats(group_name, target_date)

        monkeypatch.setattr(tool_mod, "ingest_group", fake_ingest)
        return calls

    return setup


async def test_uses_default_group_and_date(patch_tool):
    """未传参数 → 使用设置页默认群名与默认日期"""
    calls = patch_tool({"default_group": "兼职2群", "default_date": "2026-11-08"})
    text = await ingest_wechat_group_messages.ainvoke({})
    assert calls["args"] == ("兼职2群", "26/11/8")
    assert "读取完成" in text


async def test_explicit_args_win(patch_tool):
    """显式参数优先于默认值"""
    calls = patch_tool({"default_group": "兼职2群", "default_date": "2026-11-08"})
    await ingest_wechat_group_messages.ainvoke({"group_name": "兼职1群", "target_date": "26/11/3"})
    assert calls["args"] == ("兼职1群", "26/11/3")


async def test_no_group_configured(patch_tool):
    """未传群名且未配置默认群名 → 中文提示，不调用管道"""
    calls = patch_tool({})
    text = await ingest_wechat_group_messages.ainvoke({})
    assert "未配置默认读取群" in text
    assert "args" not in calls


async def test_wechat_groups_all_read(patch_tool):
    """wechat_groups 中的全部群逐个读取（优先于旧 default_group）"""
    calls = patch_tool({
        "wechat_groups": ["兼职1群", "兼职3群"], "default_group": "兼职2群",
    })
    await ingest_wechat_group_messages.ainvoke({})
    assert [item[0] for item in calls["list"]] == ["兼职1群", "兼职3群"]


async def test_wechat_access_disabled(patch_tool):
    """微信访问关闭 → 中文提示，不调用微信管道"""
    calls = patch_tool({"wechat_access_enabled": False, "default_group": "兼职2群"})
    text = await ingest_wechat_group_messages.ainvoke({})
    assert "微信访问已在设置页关闭" in text
    assert "args" not in calls


async def test_explicit_range_reads_each_day(patch_tool):
    """显式起止日期 → 逐天调用管道（含首尾）"""
    calls = patch_tool({})
    await ingest_wechat_group_messages.ainvoke({
        "group_name": "兼职1群", "target_date": "2026-11-01", "target_date_end": "2026-11-03",
    })
    assert calls["list"] == [
        ("兼职1群", "26/11/1"), ("兼职1群", "26/11/2"), ("兼职1群", "26/11/3"),
    ]


async def test_default_range_from_preferences(patch_tool):
    """未传日期 → 使用设置页默认范围"""
    calls = patch_tool({
        "wechat_groups": ["兼职2群"],
        "default_date_start": "2026-11-01",
        "default_date_end": "2026-11-02",
    })
    await ingest_wechat_group_messages.ainvoke({})
    assert calls["list"] == [("兼职2群", "26/11/1"), ("兼职2群", "26/11/2")]


async def test_range_over_30_days_rejected(patch_tool):
    """超过 30 天 → 中文错误且不调用管道"""
    calls = patch_tool({})
    text = await ingest_wechat_group_messages.ainvoke({
        "group_name": "兼职1群", "target_date": "2026-11-01", "target_date_end": "2026-12-02",
    })
    assert "最多读取 30 天" in text
    assert "list" not in calls


async def test_range_stops_on_failure(patch_tool, monkeypatch):
    """某天失败 → 停止并报告已完成天数"""
    calls = patch_tool({})

    async def failing(group_name, target_date, progress_cb=None):
        calls.setdefault("list", []).append((group_name, target_date))
        if target_date == "26/11/2":
            raise RuntimeError("boom")
        return fake_stats(group_name, target_date)

    monkeypatch.setattr(tool_mod, "ingest_group", failing)
    text = await ingest_wechat_group_messages.ainvoke({
        "group_name": "兼职1群", "target_date": "2026-11-01", "target_date_end": "2026-11-03",
    })
    assert calls["list"] == [("兼职1群", "26/11/1"), ("兼职1群", "26/11/2")]
    assert "1/3 天" in text
    assert "读取失败：兼职1群 26/11/2" in text


async def test_default_date_empty_uses_today(patch_tool):
    """默认日期为空 → 今天"""
    from datetime import date
    from agent.mcp_wechat import to_mcp_date

    calls = patch_tool({"default_group": "兼职2群", "default_date": ""})
    await ingest_wechat_group_messages.ainvoke({})
    assert calls["args"][1] == to_mcp_date(date.today())


async def test_bad_date_format(patch_tool):
    """无法识别的日期 → 中文错误"""
    calls = patch_tool({"default_group": "兼职2群", "default_date": "下周"})
    text = await ingest_wechat_group_messages.ainvoke({})
    assert "日期格式无法识别" in text
    assert "args" not in calls