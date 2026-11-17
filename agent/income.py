# -*- coding: utf-8 -*-
"""
收入估算：按佣金中值折算单次收入（心动的offer 统计口径）。

口径（与统计页 tooltip 一致）：
- 元/时：中值 × 工时（时段解析失败按 8 小时）
- 元/天：中值
- 元/次：中值
- 元/月：中值 ÷ 30（按自然日折算）
- 无佣金：0 且 unknown=True
"""
from agent.work_time import parse_work_time

MONTH_DAYS = 30
DEFAULT_HOURS = 8.0


def estimate_income(
    commission_min: float | None,
    commission_max: float | None,
    commission_unit: str | None,
    work_time: str | None,
) -> tuple[float, bool]:
    """估算单条兼职收入

    Args:
        commission_min: 佣金下限
        commission_max: 佣金上限
        commission_unit: 佣金单位（元/时、元/天、元/次、元/月）
        work_time: 工作时段文本

    Returns:
        (估算收入, 是否佣金未知)
    """
    if commission_min is None and commission_max is None:
        return 0.0, True
    lo = float(commission_min) if commission_min is not None else float(commission_max)
    hi = float(commission_max) if commission_max is not None else lo
    mid = (lo + hi) / 2

    if commission_unit == "元/时":
        parsed = parse_work_time(work_time)
        hours = parsed.hours if parsed else DEFAULT_HOURS
        return round(mid * hours, 2), False
    if commission_unit == "元/月":
        return round(mid / MONTH_DAYS, 2), False
    # 元/天、元/次、未知单位：按单次直接计
    return round(mid, 2), False