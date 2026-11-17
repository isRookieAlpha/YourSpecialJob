# -*- coding: utf-8 -*-
"""手动粘贴文本摄入测试：解析器 + 管道入库（mock 弱模型，真实测试库）。"""
from datetime import date

import pytest

import agent.ingest as ingest_mod
import agent.manual_ingest as manual_mod
from agent.manual_ingest import ingest_manual_text, parse_pasted_text
from agent.models.job import ExtractedBatch, JobExtractItem, JobMessage

GROUP = "兼职1群"
TARGET = date(2026, 11, 3)


class TestParsePastedText:
    """parse_pasted_text 解析器测试"""

    def test_wechat_header_format(self):
        """微信复制格式：发送者+日期时间行头，后续行为消息体"""
        text = (
            "张姐 2026-11-03 10:15\n"
            "酒店传菜日结110，加微信abc\n"
            "\n"
            "李哥 2026-11-03 10:20\n"
            "仓库搬货200一天"
        )
        messages = parse_pasted_text(text)
        assert len(messages) == 2
        assert messages[0]["发送者"] == "张姐"
        assert messages[0]["时间"] == "10:15"
        assert messages[0]["消息"] == "酒店传菜日结110，加微信abc"
        assert messages[1]["发送者"] == "李哥"
        assert messages[1]["消息"] == "仓库搬货200一天"

    def test_header_without_date(self):
        """行头只带时间（无日期）也识别"""
        text = "张姐 10:15\n酒店传菜日结110"
        messages = parse_pasted_text(text)
        assert len(messages) == 1
        assert messages[0]["发送者"] == "张姐"
        assert messages[0]["时间"] == "10:15"

    def test_multi_line_merged_into_one(self):
        """行头之间的多行合并为一条消息"""
        text = (
            "张姐 2026-11-03 10:15\n"
            "招聘传菜员\n"
            "时间：7:30-21:00\n"
            "地点：静安寺"
        )
        messages = parse_pasted_text(text)
        assert len(messages) == 1
        assert messages[0]["消息"] == "招聘传菜员\n时间：7:30-21:00\n地点：静安寺"

    def test_plain_text_blocks_by_blank_line(self):
        """无行头纯文本：按空行分段"""
        text = "酒店传菜日结110\n\n仓库搬货200一天"
        messages = parse_pasted_text(text)
        assert len(messages) == 2
        assert messages[0]["消息"] == "酒店传菜日结110"
        assert messages[1]["消息"] == "仓库搬货200一天"

    def test_plain_text_single_block(self):
        """无行头且无空行：整块一条消息"""
        text = "酒店传菜日结110\n加微信abc"
        messages = parse_pasted_text(text)
        assert len(messages) == 1
        assert messages[0]["消息"] == "酒店传菜日结110\n加微信abc"

    def test_empty_text_raises(self):
        """空文本抛 ValueError"""
        with pytest.raises(ValueError, match="为空"):
            parse_pasted_text("   \n  ")


def _batch(text: str) -> ExtractedBatch:
    return ExtractedBatch(items=[
        JobExtractItem(
            source_index=0,
            raw_text=text,
            is_valid_job=True,
            job=JobMessage(work_summary="传菜", commission_min=110, commission_max=110,
                           commission_unit="元/天"),
        )
    ])


class FakeExtractor:
    def __init__(self, answer: ExtractedBatch):
        self.answer = answer

    async def ainvoke(self, prompt: str) -> ExtractedBatch:
        return self.answer


@pytest.fixture
def patch_manual(monkeypatch, pool, tmp_path):
    """替换模型/连接池/jsonl 路径（手动通道不碰微信，无需 mock 读取）"""

    def setup(extractor):
        async def fake_pool():
            return pool

        monkeypatch.setattr(manual_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(ingest_mod, "get_extractor", lambda: extractor)
        monkeypatch.setattr(ingest_mod, "get_pool", fake_pool)
        monkeypatch.setattr(
            ingest_mod, "history_path", lambda group, source="wechat_auto": tmp_path / f"{source}.jsonl"
        )
        return extractor

    return setup


async def test_manual_text_ingested_with_source(patch_manual, pool):
    """手动粘贴入库：source=manual_text，DB 血缘正确"""
    patch_manual(FakeExtractor(_batch("酒店传菜日结110，加微信abc")))

    stats = await ingest_manual_text(GROUP, TARGET, "酒店传菜日结110，加微信abc")
    assert stats.new_inserted == 1
    assert stats.source_ref == "manual_paste"

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT source, source_ref, raw_text FROM jobs WHERE group_name = %s", (GROUP,)
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "manual_text"
    assert rows[0][1] == "manual_paste"


async def test_manual_and_auto_jsonl_separate(patch_manual, tmp_path, monkeypatch):
    """manual_text 与 wechat_auto 的 jsonl 文件按 source 分开"""
    extract = FakeExtractor(_batch("酒店传菜日结110"))
    patch_manual(extract)
    # 记录 history_path 实际收到的 source
    sources = []
    real_path = ingest_mod.history_path
    monkeypatch.setattr(
        ingest_mod, "history_path",
        lambda group, source="wechat_auto": sources.append(source) or tmp_path / f"{source}.jsonl",
    )
    await ingest_manual_text(GROUP, TARGET, "酒店传菜日结110")
    assert sources == ["manual_text"]


async def test_manual_repeat_is_idempotent(patch_manual):
    """重复粘贴同文本：第二遍零新入库（jsonl 去重）"""
    patch_manual(FakeExtractor(_batch("酒店传菜日结110")))
    first = await ingest_manual_text(GROUP, TARGET, "酒店传菜日结110")
    second = await ingest_manual_text(GROUP, TARGET, "酒店传菜日结110")
    assert first.new_inserted == 1
    assert second.new_inserted == 0
    assert second.dup_jsonl == 1


async def test_manual_bad_text_raises(patch_manual):
    """空粘贴 → RuntimeError 中文提示"""
    patch_manual(FakeExtractor(_batch("x")))
    with pytest.raises(RuntimeError, match="解析失败"):
        await ingest_manual_text(GROUP, TARGET, "   ")


async def test_tool_default_group_is_paste_label(monkeypatch):
    """对话粘贴未指定群名时统一记为 '手动复制粘贴识别'（不再回退设置页默认群名）"""
    from types import SimpleNamespace

    import agent.tools.manual_ingest as tool_mod
    from agent.models.job import IngestStats

    captured = {}
    monkeypatch.setattr(
        tool_mod, "get_settings",
        lambda: SimpleNamespace(preferences={"default_group": "兼职2群", "default_date": ""}),
    )
    monkeypatch.setattr(tool_mod, "get_stream_writer", lambda: (lambda payload: None))

    async def fake_ingest(group_name, target_date, text, progress_cb=None):
        captured["group"] = group_name
        return IngestStats(
            group_name=group_name, target_date=str(target_date), source_ref="manual_paste",
            total_messages=1, noise_filtered=0, batches=1, llm_valid=1, new_inserted=1,
            dup_in_run=0, dup_jsonl=0, dup_db=0, elapsed_sec=0.5,
        )

    monkeypatch.setattr(tool_mod, "ingest_manual_text", fake_ingest)
    await tool_mod.ingest_text_messages.ainvoke({"text": "酒店传菜日结110"})
    assert captured["group"] == "手动复制粘贴识别"