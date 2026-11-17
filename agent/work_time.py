# -*- coding: utf-8 -*-
"""
工作时段解析：把自由文本时段（"7:30-21:00"、"9点-18点"、"下午2点-晚上12点"）解析为起止时间。

策略：提取文本中前两个时刻，对每个时刻依据其前文中文时段词（下午/晚上）修正小时数。
解析失败返回 None（调用方按默认时段 08:00-22:00 展示或按 8 小时估算收入）。
"""
import re
from dataclasses import dataclass

#1.单个时刻：7:30 / 9点 / 18点 / 7点30
_TIME_RE = re.compile(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?")

#2.中文时段词 → 小时偏移（仅对 <12 点生效；"晚上12点"按 24:00 处理）
_PERIOD_OFFSET = {"下午": 12, "晚上": 12, "傍晚": 12, "中午": 12}

DEFAULT_START = "08:00"
DEFAULT_END = "22:00"
DEFAULT_HOURS = 8.0


@dataclass
class WorkTime:
    """解析结果：起止 HH:MM 与时长（小时）"""

    start: str
    end: str
    hours: float


def _fmt(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _apply_period(minutes: int, prefix: str) -> int:
    """依据时刻前的时段词修正（下午2点→14:00；晚上12点→24:00）"""
    for word, offset in _PERIOD_OFFSET.items():
        if word in prefix and minutes <= 12 * 60:
            return minutes + offset * 60
    return minutes


def parse_work_time(text: str | None) -> WorkTime | None:
    """解析工作时段文本

    Args:
        text: 如 "7:30-21:00"、"9点-18点"、"下午2点-晚上12点"

    Returns:
        WorkTime；无法解析返回 None
    """
    if not text:
        return None
    raw = text.strip()
    matches = list(_TIME_RE.finditer(raw))
    if len(matches) < 2:
        return None

    first, second = matches[0], matches[1]
    start_min = int(first.group(1)) * 60 + int(first.group(2) or 0)
    end_min = int(second.group(1)) * 60 + int(second.group(2) or 0)
    #3.各时刻前文（首个时刻取开头，第二个取两个时刻之间）
    start_min = _apply_period(start_min, raw[:first.start()])
    end_min = _apply_period(end_min, raw[first.end():second.start()])

    #4.跨夜（22:00-02:00）加 24 小时
    if end_min <= start_min:
        end_min += 24 * 60
    hours = (end_min - start_min) / 60
    if hours <= 0 or hours > 24:
        return None
    return WorkTime(start=_fmt(start_min), end=_fmt(end_min), hours=hours)