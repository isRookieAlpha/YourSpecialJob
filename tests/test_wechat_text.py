# -*- coding: utf-8 -*-
"""微信纯文本排版测试：Markdown 清洗、段落 emoji、前缀预算截断。"""
from agent.wechat_text import (
    WECHAT_MAX_LEN,
    add_paragraph_emoji,
    fit_wechat_message,
    strip_markdown,
    to_wechat_plain_text,
)


class TestStripMarkdown:
    def test_bold_and_italic(self):
        assert strip_markdown("**加粗** 和 *斜体*") == "加粗 和 斜体"

    def test_heading(self):
        assert strip_markdown("### 标题") == "标题"

    def test_unordered_list(self):
        assert strip_markdown("- 项目一\n* 项目二\n+ 项目三") == "• 项目一\n• 项目二\n• 项目三"

    def test_ordered_list_kept(self):
        assert strip_markdown("1. 第一条") == "1. 第一条"

    def test_link(self):
        assert strip_markdown("[官网](https://example.com)") == "官网（https://example.com）"

    def test_image(self):
        assert strip_markdown("![截图](https://x/a.png)") == "截图"

    def test_inline_code(self):
        assert strip_markdown("运行 `pytest`") == "运行 pytest"

    def test_code_fence(self):
        assert strip_markdown("```python\nprint(1)\n```") == "print(1)"

    def test_table(self):
        text = "| 兼职 | 佣金 | 通勤 |\n| --- | --- | --- |\n| 传菜 | 100元 | 骑行14分钟 |"
        assert strip_markdown(text) == "兼职：传菜\n佣金：100元\n通勤：骑行14分钟"

    def test_table_with_emoji(self):
        text = "| 兼职 | 佣金 | 状态 |\n| --- | --- | --- |\n| 传菜 | 100元 | 已心动 |"
        out = to_wechat_plain_text(text)
        assert "💼 兼职：传菜" in out
        assert "💰 佣金：100元" in out
        assert "✅ 状态：已心动" in out

    def test_multiple_table_rows(self):
        text = (
            "| 兼职 | 佣金 |\n| --- | --- |\n"
            "| 传菜 | 100元 |\n| 导购 | 200元 |"
        )
        out = strip_markdown(text)
        assert "兼职：传菜\n佣金：100元" in out
        assert "兼职：导购\n佣金：200元" in out

    def test_horizontal_rule_removed(self):
        assert strip_markdown("a\n---\nb") == "a\nb"

    def test_html_tags(self):
        assert strip_markdown("a<br>b<c>x</c>") == "a\nbx"


class TestParagraphEmoji:
    def test_known_fields(self):
        assert add_paragraph_emoji("佣金：100元") == "💰 佣金：100元"
        assert add_paragraph_emoji("工作内容：传菜") == "💼 工作内容：传菜"
        assert add_paragraph_emoji("联系方式：微信abc") == "📞 联系方式：微信abc"
        assert add_paragraph_emoji("通勤：骑行14分钟") == "🚲 通勤：骑行14分钟"
        assert add_paragraph_emoji("来源：微信群") == "📌 来源：微信群"

    def test_numbered_line(self):
        assert add_paragraph_emoji("1. 工作内容：传菜") == "1. 💼 工作内容：传菜"

    def test_existing_emoji_not_duplicated(self):
        assert add_paragraph_emoji("💰 佣金：100元") == "💰 佣金：100元"

    def test_unknown_text_unchanged(self):
        assert add_paragraph_emoji("随便一句话") == "随便一句话"


class TestToWechatPlainText:
    def test_markdown_to_plain_with_emoji(self):
        text = "**佣金**：100元\n- **地点**：静安寺"
        assert to_wechat_plain_text(text) == "💰 佣金：100元\n• 📍 地点：静安寺"

    def test_collapses_blank_lines(self):
        assert to_wechat_plain_text("a\n\n\n\nb") == "a\n\nb"

    def test_emoji_can_be_disabled(self):
        assert to_wechat_plain_text("佣金：100", add_emoji=False) == "佣金：100"


class TestFitWechatMessage:
    def test_short_message_unchanged(self):
        assert fit_wechat_message("【YourSpecialJob 回复】", "你好") == "【YourSpecialJob 回复】\n你好"

    def test_long_message_within_limit(self):
        prefix = "【YourSpecialJob 回复】"
        body = "\n".join(f"第 {i} 行内容" for i in range(500))
        message = fit_wechat_message(prefix, body, WECHAT_MAX_LEN)
        assert len(message) <= WECHAT_MAX_LEN
        assert message.startswith(prefix)
        assert "已截断" in message

    def test_truncates_on_line_boundary(self):
        prefix = "【YourSpecialJob 回复】"
        body = "第一行\n" + "长" * 3000
        message = fit_wechat_message(prefix, body, 500)
        assert "第一行" in message
        assert "长" * 10 in message

    def test_no_prefix(self):
        message = fit_wechat_message("", "x" * 2000, 1800)
        assert len(message) <= 1800