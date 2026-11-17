# -*- coding: utf-8 -*-
"""ingest 管道测试：mock 微信读取 + mock 弱模型，真实测试库。"""
import pytest

import agent.ingest as ingest_mod
from agent.models.job import ExtractedBatch, JobExtractItem, JobMessage

GROUP = "兼职1群"
DATE = "26/11/3"


def msg(i: int, text: str, t: str = "10:00") -> dict:
    return {"index": i, "发送者": "False", "时间": t, "消息": text}


def batch_of(items: list[tuple[int, str]]) -> ExtractedBatch:
    """构造：每条 (index, 原文) 都判为有效兼职"""
    return ExtractedBatch(items=[
        JobExtractItem(
            source_index=i,
            raw_text=t,
            is_valid_job=True,
            job=JobMessage(work_summary=f"工作{i}", commission_min=100, commission_max=100,
                           commission_unit="元/天"),
        )
        for i, t in items
    ])


class FakeExtractor:
    """按调用顺序返回预置结果，并记录提示词"""

    def __init__(self, answers: list[ExtractedBatch]):
        self.answers = answers
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> ExtractedBatch:
        self.prompts.append(prompt)
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]


class PromptAwareExtractor:
    """根据提示词中实际出现的消息动态返回对应提取结果（分批测试用）"""

    def __init__(self, all_messages: list[dict]):
        self.all_messages = all_messages
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> ExtractedBatch:
        self.prompts.append(prompt)
        items = [(m["index"], m["消息"]) for m in self.all_messages if m["消息"] in prompt]
        return batch_of(items)


@pytest.fixture
def patch_pipeline(monkeypatch, pool, tmp_path):
    """替换微信读取 / 模型 / 连接池 / jsonl 路径"""

    def factory(messages: list[dict], extractor):
        async def fake_read(group_name: str, target_date: str) -> list[dict]:
            return messages

        async def fake_pool():
            return pool

        monkeypatch.setattr(ingest_mod, "call_wechat_get_history", fake_read)
        monkeypatch.setattr(ingest_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(ingest_mod, "get_extractor", lambda: extractor)
        monkeypatch.setattr(ingest_mod, "get_pool", fake_pool)
        monkeypatch.setattr(ingest_mod, "find_source_ref", lambda group: "src.json")
        monkeypatch.setattr(
            ingest_mod, "history_path", lambda group, source="wechat_auto": tmp_path / "g.jsonl"
        )
        return extractor

    return factory


async def test_prefilter_counts_and_ingest(patch_pipeline):
    """噪音与刷屏重复计数正确，有效消息入库"""
    messages = [
        msg(0, "动画表情"),
        msg(1, "酒店传菜日结110，加微信abc"),
        msg(2, "酒店传菜日结110，加微信abc"),  # 刷屏重复
        msg(3, "仓库搬货200一天"),
    ]
    patch_pipeline(messages, FakeExtractor([
        batch_of([(1, "酒店传菜日结110，加微信abc"), (3, "仓库搬货200一天")])
    ]))

    stats = await ingest_mod.ingest_group(GROUP, DATE)
    assert stats.total_messages == 4
    assert stats.noise_filtered == 1
    assert stats.dup_raw == 1
    assert stats.llm_valid == 2
    assert stats.new_inserted == 2


async def test_second_run_uses_jsonl_dedup(patch_pipeline):
    """同一批消息摄入两遍：第二遍全部命中 jsonl 层，零新入库"""
    messages = [msg(0, "酒店传菜日结110，加微信abc")]
    patch_pipeline(messages, FakeExtractor([batch_of([(0, "酒店传菜日结110，加微信abc")])]))

    first = await ingest_mod.ingest_group(GROUP, DATE)
    second = await ingest_mod.ingest_group(GROUP, DATE)
    assert first.new_inserted == 1
    assert second.new_inserted == 0
    assert second.dup_jsonl == 1


async def test_model_duplicate_output_counted(patch_pipeline):
    """模型对同一条消息重复输出 → run 内去重计数"""
    messages = [msg(0, "酒店传菜日结110")]
    extractor = patch_pipeline(messages, FakeExtractor([
        batch_of([(0, "酒店传菜日结110"), (0, "酒店传菜日结110")])
    ]))
    stats = await ingest_mod.ingest_group(GROUP, DATE)
    assert extractor.prompts, "应调用过弱模型"
    assert stats.dup_in_run == 1
    assert stats.new_inserted == 1


async def test_chunking_respects_char_limit(patch_pipeline, monkeypatch):
    """超长消息不跨批切割；总字符超限时切多批"""
    monkeypatch.setattr(ingest_mod, "MAX_CHARS_PER_BATCH", 1000)
    messages = [msg(i, f"{'消息' * 300}号{i}") for i in range(6)]  # 每条 600 字符
    extractor = patch_pipeline(messages, PromptAwareExtractor(messages))

    stats = await ingest_mod.ingest_group(GROUP, DATE)
    assert stats.batches == 6  # 每条 600 字符，600+600>1000 → 每批 1 条，共 6 批
    assert len(extractor.prompts) == 6
    # 每条消息完整出现在某一批提示词中（不跨批切割）
    for m in messages:
        assert any(m["消息"] in p for p in extractor.prompts)


async def test_wechat_error_converted_to_runtime_error(monkeypatch):
    """微信读取失败 → RuntimeError 中文降级提示"""

    async def failing_read(group_name: str, target_date: str):
        raise RuntimeError("微信未登录")

    monkeypatch.setattr(ingest_mod, "is_model_configured", lambda kind: True)
    monkeypatch.setattr(ingest_mod, "call_wechat_get_history", failing_read)

    with pytest.raises(RuntimeError, match="微信读取失败"):
        await ingest_mod.ingest_group(GROUP, DATE)

def test_publish_time_overridden_by_message_time():
    """模型把本地时间误标 UTC（...Z）时，以微信消息时刻重新合成（修复 +8 小时偏移）"""
    from datetime import date, datetime, timezone

    from agent.ingest import _reconcile_raw_text
    from agent.models.job import ExtractedBatch, JobExtractItem, JobMessage

    msg = {"index": 0, "发送者": "张姐", "时间": "11:38", "消息": "酒店传菜日结110"}
    item = JobExtractItem(
        source_index=0,
        raw_text="酒店传菜日结110",
        is_valid_job=True,
        job=JobMessage(
            work_summary="酒店传菜",
            publish_time=datetime(2026, 11, 6, 11, 38, tzinfo=timezone.utc),  # 模型错误标 Z
        ),
    )
    result = _reconcile_raw_text([msg], ExtractedBatch(items=[item]), date(2026, 11, 6))
    pub = result[0].job.publish_time
    assert pub.utcoffset() is not None
    assert (pub.hour, pub.minute) == (11, 38)  # 本地 11:38，而非 19:38
