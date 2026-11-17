# -*- coding: utf-8 -*-
"""
来源与时间格式化（单一真相源）。

工具文本、SSE offers、offers API、PDF 全部调用 format_source()，
避免四套文案不一致。
"""
from datetime import datetime


def format_dt(value) -> str:
    """时间值 → 本地时区 `MM-DD HH:mm`（容忍 None/字符串/naive datetime）

    Args:
        value: datetime / ISO 字符串 / None

    Returns:
        格式化字符串；无法解析时返回空字符串
    """
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value  # 已是普通字符串，原样返回
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone()
        return value.strftime("%m-%d %H:%M")
    return str(value)


def format_commute(row: dict) -> str:
    """通勤摘要（距离/骑行/公共交通/驾车；未计算时返回"通勤未计算"）"""
    parts: list[str] = []
    distance = row.get("commute_distance_m")
    if distance:
        parts.append(f"距离{distance / 1000:.1f}km")
    if row.get("commute_bike_min") is not None:
        parts.append(f"骑行{row['commute_bike_min']}分钟")
    if row.get("commute_transit_min") is not None:
        parts.append(f"公交/地铁{row['commute_transit_min']}分钟")
    if row.get("commute_drive_min") is not None:
        parts.append(f"驾车{row['commute_drive_min']}分钟")
    return " / ".join(parts) if parts else "通勤未计算"


def format_source(row: dict) -> str:
    """生成来源描述文案

    Args:
        row: jobs 查询行（需 source/group_name/ingested_at，可选 publish_time）

    Returns:
        如 `微信群「兼职1群」自动读取（采集于 11-04 20:15）`
    """
    source = row.get("source") or ""
    group = (row.get("group_name") or "").strip()
    when = format_dt(row.get("ingested_at")) or format_dt(row.get("publish_time"))
    when_text = f"（采集于 {when}）" if when else ""

    if source == "wechat_auto":
        base = f"微信群「{group}」自动读取" if group else "微信群自动读取"
    elif source == "manual_text":
        # 内部占位群名（手动复制粘贴识别/手动粘贴）不对外展示
        if group and group not in ("手动粘贴", "手动复制粘贴识别"):
            base = f"手动粘贴（群「{group}」）"
        else:
            base = "手动粘贴"
    elif source == "manual_image":
        # 截图批次的群名（图片上传_时间戳）只是内部过滤标识，不对外展示
        if group and not group.startswith("图片上传") and group != "手动粘贴":
            base = f"截图识别（群「{group}」）"
        else:
            base = "截图识别"
    elif source == "manual_form":
        base = "手动录入"
    else:
        base = source or "未知来源"

    text = f"{base}{when_text}"
    pub = format_dt(row.get("publish_time"))
    if pub and source != "manual_form":
        text += f"；消息发布 {pub}"
    return text