# -*- coding: utf-8 -*-
"""工作时段解析测试。"""
from agent.work_time import parse_work_time


def test_standard_range():
    r = parse_work_time("7:30-21:00")
    assert (r.start, r.end, r.hours) == ("07:30", "21:00", 13.5)


def test_full_width_colon():
    r = parse_work_time("9：00~18：00")
    assert (r.start, r.end, r.hours) == ("09:00", "18:00", 9.0)


def test_dian_style():
    r = parse_work_time("9点-18点")
    assert (r.start, r.end, r.hours) == ("09:00", "18:00", 9.0)


def test_period_prefix():
    r = parse_work_time("下午2点-晚上12点")
    assert (r.start, r.end) == ("14:00", "00:00")
    assert r.hours == 10.0


def test_overnight():
    r = parse_work_time("22:00-02:00")
    assert r.hours == 4.0


def test_unparsable_returns_none():
    assert parse_work_time("随时") is None
    assert parse_work_time("") is None
    assert parse_work_time(None) is None