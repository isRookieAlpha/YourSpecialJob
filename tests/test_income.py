# -*- coding: utf-8 -*-
"""收入估算测试（佣金中值口径）。"""
from agent.income import estimate_income


def test_hourly_with_work_time():
    # 30 元/时 × 13.5h
    income, unknown = estimate_income(30, 30, "元/时", "7:30-21:00")
    assert income == 405.0
    assert not unknown


def test_hourly_without_work_time_uses_8h():
    income, _ = estimate_income(30, 40, "元/时", None)
    assert income == 35 * 8.0


def test_daily():
    assert estimate_income(110, 110, "元/天", None) == (110.0, False)


def test_per_task():
    assert estimate_income(200, 260, "元/次", None) == (230.0, False)


def test_monthly_divided_by_30():
    assert estimate_income(3000, 3000, "元/月", None) == (100.0, False)


def test_no_commission_unknown():
    assert estimate_income(None, None, None, None) == (0.0, True)


def test_min_only_and_max_only():
    assert estimate_income(100, None, "元/天", None) == (100.0, False)
    assert estimate_income(None, 120, "元/天", None) == (120.0, False)