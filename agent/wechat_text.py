# -*- coding: utf-8 -*-
"""微信消息排版：Markdown → 纯文本 + 段落 emoji（微信不渲染 Markdown）。

设计原则：
- 只服务微信发送边界（推送摘要、互动回复），不污染网页/PDF/API 共用的 formatting 模块
- 清洗用标准库 re，不引入新依赖
- 消息前缀（【YourSpecialJob）必须保持在第一个字符；截断预算把前缀计入
"""
import re

WECHAT_MAX_LEN = 1800  # 微信单条消息 <2000 字，留余量

#1.字段 → emoji（按顺序匹配，先命中先用；"工作时间"归入时间）
_FIELD_EMOJI_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("时间", "日期", "时段", "班次"), "⏰"),
    (("佣金", "薪资", "工资", "报酬", "收入", "报价"), "💰"),
    (("联系", "联系方式", "报名", "咨询", "电话", "微信"), "📞"),
    (("地点", "地址", "位置", "工作地"), "📍"),
    (("通勤", "距离"), "🚲"),
    (("体力", "强度"), "💪"),
    (("来源",), "📌"),
    (("天气",), "🌤"),
    (("备注", "其他", "说明"), "📝"),
    (("押金",), "💳"),
    (("结算", "支付"), "💵"),
    (("状态",), "✅"),
    (("兼职", "工作内容", "兼职内容", "岗位", "工作"), "💼"),
)

#2.行首可选列表符号/序号 + 字段名 + 中英文冒号
_LIST_PREFIX_RE = re.compile(r"^(?:[•·\-]\s*|\d{1,2}[.、]\s*)?")
_FIELD_RE = re.compile(r"^(?:[•·\-]\s*|\d{1,2}[.、]\s*)?([^：:\n]{1,12})[：:]")
_EMOJI_HEAD_RE = re.compile(
    "^[\U0001F000-\U0001FAFF☀-➿⭐✅❌❗❓]"
)
_CODE_FENCE_RE = re.compile(r"^\s*```")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)|(?<!_)_([^_\n]+?)_(?!_)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_LIST_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_]\s*){3,}$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _emoji_for(field: str) -> str:
    """根据字段名选择 emoji（无匹配返回空串）"""
    name = field.strip()
    for keywords, emoji in _FIELD_EMOJI_RULES:
        if any(k in name for k in keywords):
            return emoji
    return ""


def _strip_inline(line: str) -> str:
    """行内 Markdown 清洗（图片/链接/粗体/斜体/行内代码/HTML）"""
    line = _IMAGE_RE.sub(lambda m: m.group(1) or "图片", line)
    line = _LINK_RE.sub(
        lambda m: f"{m.group(1)}（{m.group(2)}）" if m.group(1) else m.group(2), line
    )
    line = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", line)
    line = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", line)
    line = _INLINE_CODE_RE.sub(r"\1", line)
    line = _HTML_BR_RE.sub("\n", line)
    line = _HTML_TAG_RE.sub("", line)
    return line


def _format_table(header: list[str], rows: list[list[str]]) -> list[str]:
    """Markdown 表格 → 每条数据逐行「字段：值」（微信里可读，后续可加 emoji）"""
    output: list[str] = []
    for row in rows:
        pairs: list[str] = []
        for index, cell in enumerate(row):
            value = _strip_inline(str(cell).strip())
            if not value:
                continue
            key = _strip_inline(header[index].strip()) if index < len(header) else ""
            if key and key not in ("#", "序号"):
                pairs.append(f"{key}：{value}")
            else:
                pairs.append(value)
        if pairs:
            if output:
                output.append("")  # 条目之间空行
            output.extend(pairs)
    return output


def strip_markdown(text: str) -> str:
    """把 Markdown 文本清洗为纯文本（保留内容与换行结构）"""
    lines: list[str] = []
    in_code = False
    table_header: list[str] | None = None
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_header, table_rows
        if table_header is not None:
            lines.extend(_format_table(table_header, table_rows))
        table_header, table_rows = None, []

    for raw_line in str(text or "").splitlines():
        #3.代码围栏：去掉围栏行，内容原样保留
        if _CODE_FENCE_RE.match(raw_line):
            flush_table()
            in_code = not in_code
            continue
        if in_code:
            lines.append(raw_line)
            continue
        #4.表格：表头+数据行转成「字段：值」，分隔行删除
        if _TABLE_ROW_RE.match(raw_line):
            if _TABLE_SEP_RE.match(raw_line) and "-" in raw_line:
                continue
            cells = [c.strip() for c in raw_line.strip().strip("|").split("|")]
            if table_header is None:
                table_header = cells
            else:
                table_rows.append(cells)
            continue
        flush_table()
        #5.标题/引用/水平线/无序列表
        line = _HEADING_RE.sub("", raw_line)
        line = _QUOTE_RE.sub("", line)
        if _HR_RE.match(line):
            continue
        if _LIST_RE.match(line):
            line = _LIST_RE.sub(lambda m: f"{m.group(1)}• {m.group(2)}", line)
        lines.append(_strip_inline(line))
    flush_table()
    return "\n".join(lines)


def add_paragraph_emoji(text: str) -> str:
    """给「字段：内容」段落加 emoji（已有 emoji 的行跳过，避免重复）"""
    result: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or _EMOJI_HEAD_RE.match(stripped):
            result.append(line)
            continue
        match = _FIELD_RE.match(stripped)
        if match:
            emoji = _emoji_for(match.group(1))
            if emoji:
                #1.保留行首的序号/列表符号，emoji 放在字段名前
                prefix = _LIST_PREFIX_RE.match(stripped).group(0)
                rest = stripped[len(prefix):]
                result.append(f"{prefix}{emoji} {rest}")
                continue
        result.append(line)
    return "\n".join(result)


def to_wechat_plain_text(text: str, *, add_emoji: bool = True) -> str:
    """Markdown → 微信纯文本（清洗 + 段落 emoji + 折叠多余空行）"""
    cleaned = strip_markdown(text)
    if add_emoji:
        cleaned = add_paragraph_emoji(cleaned)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def fit_wechat_message(
    prefix: str,
    body: str,
    limit: int = WECHAT_MAX_LEN,
    suffix: str = "\n…（内容过长已截断）",
) -> str:
    """把前缀 + 正文控制在 limit 内（优先按行截断，前缀始终保留）"""
    message = f"{prefix}\n{body}" if prefix else body
    if len(message) <= limit:
        return message
    #1.预算扣除前缀、换行与截断后缀
    budget = limit - len(prefix) - (1 if prefix else 0) - len(suffix)
    if budget <= 0:
        return message[:limit]
    #2.按行累加，保留完整行
    kept: list[str] = []
    used = 0
    for line in body.splitlines():
        extra = len(line) + (1 if kept else 0)
        if used + extra <= budget:
            kept.append(line)
            used += extra
            continue
        #2.放不下的行：若仍有剩余预算则截断该行，尽量保留内容
        remaining = budget - used - (1 if kept else 0)
        if remaining > 0:
            kept.append(line[:remaining])
        break
    trimmed = "\n".join(kept).rstrip() or body[:budget]
    return f"{prefix}\n{trimmed}{suffix}" if prefix else f"{trimmed}{suffix}"