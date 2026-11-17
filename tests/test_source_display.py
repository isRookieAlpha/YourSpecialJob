# -*- coding: utf-8 -*-
"""来源文案格式化测试（format_source / format_dt）。"""
from datetime import datetime, timezone, timedelta

from agent.formatting import format_dt, format_source

CST = timezone(timedelta(hours=8))


def row(source: str, **kwargs) -> dict:
    base = {
        "source": source,
        "group_name": "兼职1群",
        "ingested_at": datetime(2026, 11, 4, 20, 15, tzinfo=CST),
        "publish_time": None,
    }
    base.update(kwargs)
    return base


class TestFormatDt:
    def test_aware_datetime_to_local(self):
        assert format_dt(datetime(2026, 11, 4, 20, 15, tzinfo=CST)) == "11-04 20:15"

    def test_iso_string(self):
        assert format_dt("2026-11-04T20:15:00+08:00") == "11-04 20:15"

    def test_none_and_garbage(self):
        assert format_dt(None) == ""
        assert format_dt("不是时间") == "不是时间"


class TestFormatSource:
    def test_wechat_auto(self):
        text = format_source(row("wechat_auto"))
        assert "微信群「兼职1群」自动读取" in text
        assert "采集于 11-04 20:15" in text

    def test_manual_text(self):
        text = format_source(row("manual_text"))
        assert "手动粘贴" in text
        assert "群「兼职1群」" in text
        assert "采集于 11-04 20:15" in text

    def test_manual_image(self):
        text = format_source(row("manual_image"))
        assert "截图识别" in text
        assert "采集于 11-04 20:15" in text

    def test_manual_form(self):
        text = format_source(row("manual_form", group_name="手动录入"))
        assert text.startswith("手动录入")
        assert "采集于 11-04 20:15" in text

    def test_publish_time_appended(self):
        text = format_source(row("wechat_auto", publish_time=datetime(2026, 11, 3, 10, 15, tzinfo=CST)))
        assert "消息发布 11-03 10:15" in text

    def test_missing_ingested_at(self):
        text = format_source(row("manual_text", ingested_at=None, publish_time=None))
        assert "采集于" not in text

    def test_unknown_source(self):
        assert "custom_src" in format_source(row("custom_src"))

    def test_manual_image_batch_group_hidden(self):
        """截图批次的内部群名（图片上传_时间戳）不在来源文案中暴露"""
        text = format_source(row("manual_image", group_name="图片上传_20261105_153012"))
        assert text.startswith("截图识别")
        assert "图片上传_" not in text

    def test_manual_text_paste_label_hidden(self):
        """手动粘贴的内部标识（手动复制粘贴识别）不在来源文案中暴露"""
        text = format_source(row("manual_text", group_name="手动复制粘贴识别"))
        assert text.startswith("手动粘贴")
        assert "手动复制粘贴识别" not in text