# -*- coding: utf-8 -*-
"""跟踪配置解析测试。"""
from datetime import date, timedelta

import pytest

from agent.preferences import (
    DEFAULT_WORK_END,
    DEFAULT_WORK_START,
    MAX_DEFAULT_READ_DAYS,
    ContactInteractionConfig,
    InteractionConfig,
    TrackingConfig,
    load_chat_preferences,
    load_contact_interaction_config,
    load_interaction_config,
    load_tracking_config,
    parse_groups,
    parse_hhmm,
    parse_labor_level,
    parse_optional_hours,
    parse_optional_minutes,
    resolve_default_date_range,
    resolve_wechat_contacts,
    resolve_wechat_groups,
)


class TestParseGroups:
    def test_list_input(self):
        assert parse_groups(["兼职1群", "兼职2群", "兼职1群", " "]) == ["兼职1群", "兼职2群"]

    def test_comma_and_newline(self):
        assert parse_groups("兼职1群, 兼职2群\n兼职3群，兼职4群") == [
            "兼职1群", "兼职2群", "兼职3群", "兼职4群",
        ]

    def test_max_ten(self):
        groups = parse_groups([f"群{i}" for i in range(15)])
        assert len(groups) == 10

    def test_invalid(self):
        assert parse_groups(None) == []
        assert parse_groups(123) == []


class TestLoadTrackingConfig:
    def test_defaults(self):
        cfg = load_tracking_config(None)
        assert cfg == TrackingConfig()
        assert cfg.enabled is False
        assert cfg.interval_min == 15
        assert cfg.groups == []
        assert cfg.paused_reason == "跟踪未启用"

    def test_enabled_without_groups(self):
        cfg = load_tracking_config({"tracking_enabled": True})
        assert cfg.paused_reason == "未配置跟踪群"

    def test_clamping(self):
        cfg = load_tracking_config({
            "tracking_enabled": True,
            "tracking_interval_min": 1,
            "tracking_push_limit": 99,
            "tracking_ledger_days": 0,
            "tracking_max_messages": 99999,
            "tracking_push_lookback_min": 5,
        })
        assert cfg.interval_min == 5
        assert cfg.push_limit == 10
        assert cfg.ledger_days == 1
        assert cfg.max_messages == 1000
        assert cfg.push_lookback_min == 60

    def test_full_config(self):
        cfg = load_tracking_config({
            "tracking_enabled": True,
            "tracking_groups": "兼职1群\n兼职2群",
            "tracking_interval_min": 30,
            "tracking_push_enabled": False,
            "commute_origin": "静安寺",
            "amap_city": "上海",
            "max_commute_min": 30,
            "commission_min": 100,
            "prefer_light": True,
        })
        assert cfg.enabled and cfg.groups == ["兼职1群", "兼职2群"]
        assert cfg.interval_min == 30
        assert cfg.push_enabled is False
        assert cfg.commute_origin == "静安寺"
        assert cfg.commission_min == 100.0
        assert cfg.paused_reason == ""

    def test_invalid_commission(self):
        cfg = load_tracking_config({"commission_min": "abc"})
        assert cfg.commission_min is None


class TestResolveWechatGroups:
    def test_new_key_wins(self):
        prefs = {"wechat_groups": ["A"], "tracking_groups": ["B"], "default_group": "C"}
        assert resolve_wechat_groups(prefs) == ["A"]

    def test_legacy_fallbacks(self):
        assert resolve_wechat_groups({"tracking_groups": ["B"], "default_group": "C"}) == ["B"]
        assert resolve_wechat_groups({"default_group": "C"}) == ["C"]
        assert resolve_wechat_groups({}) == []


class TestOptionalParsers:
    def test_optional_minutes(self):
        assert parse_optional_minutes(None) is None
        assert parse_optional_minutes("") is None
        assert parse_optional_minutes(0) is None
        assert parse_optional_minutes(-5) is None
        assert parse_optional_minutes(45) == 45
        assert parse_optional_minutes(999) == 240

    def test_optional_hours(self):
        assert parse_optional_hours(None) is None
        assert parse_optional_hours("") is None
        assert parse_optional_hours(0) is None
        assert parse_optional_hours("4.5") == 4.5

    def test_hhmm(self):
        assert parse_hhmm("8:00", DEFAULT_WORK_START) == "08:00"
        assert parse_hhmm("22:30", DEFAULT_WORK_END) == "22:30"
        assert parse_hhmm("25:00", DEFAULT_WORK_START) == DEFAULT_WORK_START
        assert parse_hhmm(None, DEFAULT_WORK_END) == DEFAULT_WORK_END

    def test_labor_level(self):
        assert parse_labor_level("light") == "light"
        assert parse_labor_level("heavy") == "heavy"
        assert parse_labor_level(None, True) == "light"
        assert parse_labor_level(None, False) == "any"
        assert parse_labor_level("bogus") == "any"


class TestDefaultDateRange:
    def test_defaults_to_today(self):
        assert resolve_default_date_range({}) == (date.today(), date.today())

    def test_single_legacy_date(self):
        start, end = resolve_default_date_range({"default_date": "2026-11-01"})
        assert start == end == date(2026, 11, 1)

    def test_explicit_range(self):
        start, end = resolve_default_date_range({
            "default_date_start": "2026-11-01", "default_date_end": "2026-11-07",
        })
        assert (end - start).days + 1 == 7

    def test_30_days_allowed(self):
        start = date(2026, 11, 1)
        end = start + timedelta(days=MAX_DEFAULT_READ_DAYS - 1)
        result = resolve_default_date_range({
            "default_date_start": start.isoformat(), "default_date_end": end.isoformat(),
        })
        assert result == (start, end)

    def test_more_than_30_days_rejected(self):
        start = date(2026, 11, 1)
        end = start + timedelta(days=MAX_DEFAULT_READ_DAYS)
        with pytest.raises(ValueError, match="最多"):
            resolve_default_date_range({
                "default_date_start": start.isoformat(), "default_date_end": end.isoformat(),
            })

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="不能早于"):
            resolve_default_date_range({
                "default_date_start": "2026-11-07", "default_date_end": "2026-11-01",
            })


class TestWechatAccess:
    def test_tracking_paused_when_access_disabled(self):
        cfg = load_tracking_config({
            "wechat_access_enabled": False, "tracking_enabled": True, "tracking_groups": ["A"],
        })
        assert cfg.paused_reason == "微信访问已关闭"

    def test_interaction_paused_when_access_disabled(self):
        cfg = load_interaction_config({
            "wechat_access_enabled": False, "interaction_enabled": True, "wechat_target": "Alpha",
        })
        assert cfg.paused_reason == "微信访问已关闭"

    def test_new_commute_limits_and_labor(self):
        cfg = load_tracking_config({
            "max_commute_min": 45,
            "max_commute_transit_min": 60,
            "max_commute_drive_min": 0,
            "labor_level": "heavy",
        })
        assert cfg.max_commute_min == 45
        assert cfg.max_commute_transit_min == 60
        assert cfg.max_commute_drive_min is None
        assert cfg.labor_level == "heavy"
        assert cfg.prefer_light is False

    def test_unset_commute_limits_default_none(self):
        cfg = load_tracking_config({})
        assert cfg.max_commute_min is None
        assert cfg.max_commute_transit_min is None
        assert cfg.max_commute_drive_min is None

class TestContactPreferences:
    def test_resolve_excludes_target_and_groups(self):
        contacts = resolve_wechat_contacts({
            "wechat_contacts": ["中介小王", "Alpha", "兼职1群", "张姐", "中介小王"],
            "wechat_target": "Alpha",
            "wechat_groups": ["兼职1群"],
        })
        assert contacts == ["中介小王", "张姐"]

    def test_config_defaults(self):
        cfg = load_contact_interaction_config({})
        assert cfg == ContactInteractionConfig()
        assert cfg.enabled is False
        assert cfg.interval_sec == 60
        assert cfg.contacts == []
        assert cfg.paused_reason == "中介互动未启用"

    def test_config_clamp_and_paused(self):
        cfg = load_contact_interaction_config({
            "contact_interaction_enabled": True,
            "contact_interaction_interval_sec": 5,
            "wechat_contacts": ["中介小王"],
            "wechat_access_enabled": False,
        })
        assert cfg.interval_sec == 30
        assert cfg.paused_reason == "微信访问已关闭"

    def test_config_interval_upper_bound(self):
        cfg = load_contact_interaction_config({
            "contact_interaction_enabled": True,
            "contact_interaction_interval_sec": 999,
            "wechat_contacts": ["中介小王"],
        })
        assert cfg.interval_sec == 300

    def test_config_without_contacts(self):
        cfg = load_contact_interaction_config({
            "contact_interaction_enabled": True,
        })
        assert cfg.paused_reason == "未配置中介名单"

    def test_chat_preferences_include_contacts(self):
        prefs = load_chat_preferences({
            "wechat_contacts": ["中介小王"],
            "wechat_target": "Alpha",
        })
        assert prefs.wechat_contacts == ["中介小王"]

    def test_parse_hhmm_handles_midnight_end(self):
        """滑块右端 24:00 归一为 23:59（否则回退默认 22:00，界面与提示词不一致）"""
        assert parse_hhmm("24:00", "22:00") == "23:59"
        assert parse_hhmm("23:59", "22:00") == "23:59"
        assert parse_hhmm("bad", "22:00") == "22:00"

    def test_chat_preferences_gender_age(self):
        """性别/年龄作为显式偏好（非法值降级）"""
        prefs = load_chat_preferences({"gender": "female", "age": 22})
        assert prefs.gender == "female" and prefs.age == 22

        assert load_chat_preferences({"gender": "x", "age": "abc"}).gender == "any"
        assert load_chat_preferences({"gender": "x", "age": "abc"}).age is None
        assert load_chat_preferences({"age": 5}).age is None  # 超出 16~80 视为未填写
        assert load_chat_preferences({}).gender == "any" and load_chat_preferences({}).age is None
