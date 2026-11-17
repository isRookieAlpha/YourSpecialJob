# -*- coding: utf-8 -*-
"""job.py 模型与 hash 函数测试（不依赖数据库）。"""
import pytest
from pydantic import ValidationError

from agent.models.job import (
    ExtractedBatch,
    JobExtractItem,
    JobMessage,
    compute_content_hash,
    normalize_text,
)


def test_hash_stable_after_whitespace_normalization():
    """空白差异（首尾/内部折叠）不改变 hash"""
    h1 = compute_content_hash("兼职1群", "酒店传菜 日结110\n加微信abc")
    h2 = compute_content_hash("兼职1群", "  酒店传菜  日结110 加微信abc  ")
    assert h1 == h2


def test_hash_differs_by_group_and_content():
    """不同群或不同内容产生不同 hash"""
    assert compute_content_hash("兼职1群", "传菜") != compute_content_hash("兼职2群", "传菜")
    assert compute_content_hash("兼职1群", "传菜") != compute_content_hash("兼职1群", "洗碗")


def test_normalize_text_collapses_whitespace():
    """归一化：去首尾空白、内部连续空白折叠为单个空格"""
    assert normalize_text("  a  b\n\nc\t") == "a b c"


def test_enum_rejects_invalid_intensity():
    """非法体力强度被 Pydantic 拦截"""
    with pytest.raises(ValidationError):
        JobMessage(physical_intensity="超重")


def test_enum_rejects_invalid_commission_unit():
    """非法佣金单位被 Pydantic 拦截"""
    with pytest.raises(ValidationError):
        JobMessage(commission_unit="元/年")


def test_extracted_batch_default_empty_items():
    """无有效兼职时 items 可为空列表"""
    batch = ExtractedBatch.model_validate({"items": []})
    assert batch.items == []


def test_extract_item_roundtrip():
    """提取项可完整序列化回 dict（jsonl 存储依赖）"""
    item = JobExtractItem(
        raw_text="明天传菜 110 日结，加微信 abc123",
        is_valid_job=True,
        job=JobMessage(work_summary="传菜", commission_min=90, commission_max=110,
                       commission_unit="元/天", is_daily_settlement=True),
    )
    data = item.model_dump(mode="json")
    restored = JobExtractItem.model_validate(data)
    assert restored.job.commission_unit == "元/天"
    assert restored.is_valid_job is True