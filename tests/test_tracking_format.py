# -*- coding: utf-8 -*-
"""推送摘要格式化测试。"""
from datetime import datetime

from agent.tracking_format import MAX_LEN, build_push_summary


def row(**kwargs):
    base = {
        "id": 1,
        "group_name": "兼职1群",
        "source": "wechat_auto",
        "work_summary": "酒店传菜",
        "work_date": "2026-11-06",
        "work_time": "7:30-21:00",
        "commission_min": 110.0,
        "commission_max": 110.0,
        "commission_unit": "元/天",
        "commission_text": "日结110",
        "location": "静安寺",
        "physical_intensity": "轻",
        "contact": "微信abc",
        "publish_time": "2026-11-05T10:15:00+08:00",
        "ingested_at": "2026-11-05T14:28:00+08:00",
        "commute_bike_min": 14,
        "commute_distance_m": 3200,
        "commute_over_limit": False,
        "commute_unknown": False,
        "raw_text": "很长的原文" * 100,
    }
    base.update(kwargs)
    return base


NOW = datetime(2026, 11, 5, 14, 30)


class TestBuildPushSummary:
    def test_contains_required_sections(self):
        text = build_push_summary([row()], total_candidates=1, weather_text="天气：上海 多云 18℃", now=NOW)
        assert text.startswith("【YourSpecialJob 兼职跟踪】11-05 14:30")
        assert "新增 1 条符合偏好的兼职（群：兼职1群）" in text
        assert "酒店传菜（id=1）" in text  # 带 id，用户按编号回复时 agent 可直接映射
        assert "110元/天（约110元/次）" in text
        assert "骑行14分钟" in text
        assert "联系：微信abc" in text
        assert "来源：" in text and "微信群「兼职1群」自动读取" in text
        assert "天气：上海 多云 18℃" in text
        assert "raw_text" not in text
        assert "很长的原文" not in text

    def test_over_limit_annotation(self):
        text = build_push_summary([row(commute_over_limit=True, commute_bike_min=52)], 1, now=NOW)
        assert "骑行52分钟（超通勤上限）" in text

    def test_commute_unknown(self):
        text = build_push_summary(
            [row(commute_bike_min=None, commute_distance_m=None, commute_unknown=True)], 1, now=NOW
        )
        assert "通勤未计算" in text

    def test_overflow_hint(self):
        text = build_push_summary([row()], total_candidates=5, now=NOW)
        assert "另有 4 条未展示" in text

    def test_unknown_commission(self):
        text = build_push_summary(
            [row(commission_min=None, commission_max=None, commission_text=None)], 1, now=NOW
        )
        assert "佣金未知" in text

    def test_truncation(self):
        rows = [row(id=i, work_summary=f"兼职{i}") for i in range(10)]
        text = build_push_summary(rows, total_candidates=10, now=NOW)
        assert len(text) <= MAX_LEN