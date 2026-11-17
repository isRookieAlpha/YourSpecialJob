# -*- coding: utf-8 -*-
"""推送摘要生成（模板拼接，不调用大模型，稳定可控）。"""
from datetime import datetime

from agent.formatting import format_commute, format_dt, format_source
from agent.income import estimate_income
from agent.wechat_text import fit_wechat_message

MAX_LEN = 1800  # 微信单条消息 <2000 字，留余量


def _commission(row: dict) -> str:
    """佣金展示：区间/单值/原文回退"""
    lo, hi = row.get("commission_min"), row.get("commission_max")
    unit = row.get("commission_unit") or ""
    if lo is None and hi is None:
        return row.get("commission_text") or "佣金未知"
    if lo is not None and hi is not None and lo != hi:
        return f"{lo:g}~{hi:g}{unit}"
    return f"{(lo if lo is not None else hi):g}{unit}"


def _commute_text(row: dict) -> str:
    text = format_commute(row)
    if row.get("commute_over_limit"):
        text += "（超通勤上限）"
    return text


def build_push_summary(
    rows: list[dict],
    total_candidates: int,
    weather_text: str = "",
    now: datetime | None = None,
) -> str:
    """生成一条推送摘要（≤1800 字）

    Args:
        rows: 已筛选并注解通勤的候选行（按推荐顺序）
        total_candidates: 候选总数（用于"另有 N 条未展示"）
        weather_text: 天气一行（可空）
        now: 生成时间（测试可注入）

    Returns:
        摘要文本
    """
    now = now or datetime.now()
    groups = sorted({r.get("group_name") for r in rows if r.get("group_name")})
    lines = [
        f"【YourSpecialJob 兼职跟踪】{now.strftime('%m-%d %H:%M')}",
        f"新增 {len(rows)} 条符合偏好的兼职" + (f"（群：{'、'.join(groups)}）" if groups else ""),
    ]

    for i, row in enumerate(rows, 1):
        #1.每条兼职按字段分段（每段一行 + emoji，微信不渲染 Markdown）
        #  工作内容行附带 id：用户按编号回复收藏时，agent 可直接映射到准确条目
        job_id = row.get("id")
        id_text = f"（id={job_id}）" if job_id is not None else ""
        lines.append(f"{i}. 💼 工作内容：{row.get('work_summary') or '未命名'}{id_text}")
        lines.append(
            f"⏰ 时间：{row.get('work_date') or '日期未知'} "
            f"{row.get('work_time') or '时段未知'}"
        )
        income = estimate_income(
            row.get("commission_min"), row.get("commission_max"),
            row.get("commission_unit"), row.get("work_time"),
        )[0]
        lines.append(f"💰 佣金：{_commission(row)}（约{income:g}元/次）")
        lines.append(f"📍 地点：{row.get('location') or '地点未知'}")
        commute = _commute_text(row)
        if commute:
            lines.append(f"🚲 通勤：{commute}")
        if row.get("physical_intensity"):
            lines.append(f"💪 体力：{row['physical_intensity']}")
        if row.get("contact"):
            lines.append(f"📞 联系：{row['contact']}")
        lines.append(f"📌 来源：{format_source(row)}")
        lines.append("")

    if weather_text:
        lines.append(f"🌤 {weather_text}")
    overflow = total_candidates - len(rows)
    if overflow > 0:
        lines.append(f"另有 {overflow} 条未展示，请打开 YourSpecialJob 查看。")

    text = "\n".join(lines).strip()
    #2.截断时把标题与前缀计入预算（首字符必须是【，供方向识别）
    return fit_wechat_message(
        "", text, MAX_LEN, suffix="\n…（内容过长已截断，请打开应用查看）"
    )