# -*- coding: utf-8 -*-
"""MCP 聊天记录文本解析测试：多行消息正文里的"时间:"字样不得覆盖真实消息时刻。"""
from datetime import date

from agent.mcp_wechat import combine_publish_time, parse_history_text

TEXT = """获取到 2 条与 兼职2群 在 26/11/9 的聊天记录

发送者: 群主
时间: 21:26
消息: #康师傅协助活动
总工资:320（含鸭）
时间: 11🈷️11.12（连坐）
9:00-18:00
地点: 南区吾馨食堂
------------------------------
发送者: 小明
时间: 22:01
消息: 普通消息
------------------------------
"""


class TestParseHistoryText:
    def test_multiline_message_keeps_true_time(self):
        """正文中的"时间: …"行是消息内容，不能覆盖元数据时间（昨晚 05:37 时间错乱根因）"""
        messages = parse_history_text(TEXT)
        assert len(messages) == 2
        assert messages[0]["时间"] == "21:26"
        assert messages[0]["消息"].splitlines()[0] == "#康师傅协助活动"
        assert "时间: 11🈷️11.12（连坐）" in messages[0]["消息"]
        assert messages[1]["时间"] == "22:01"

    def test_combine_publish_time_from_parsed(self):
        """修复后能合成正确发布时间（而非保留模型误标的 UTC 值）"""
        messages = parse_history_text(TEXT)
        pub = combine_publish_time(messages[0]["时间"], date(2026, 11, 9))
        assert pub is not None
        assert (pub.hour, pub.minute) == (21, 26)
        assert pub.utcoffset() is not None
