# -*- coding: utf-8 -*-
"""
跟踪配置解析：从 settings.preferences（自由 JSON）安全地取出跟踪参数并夹取范围。

键名（全部存 app_settings.preferences，无需改 settings_dao 白名单）：
- tracking_enabled / tracking_interval_min / tracking_groups
- tracking_push_enabled / tracking_push_limit / tracking_ledger_days
- tracking_max_messages / tracking_push_lookback_min / commute_origin
复用已有：max_commute_min / amap_city / commission_min / prefer_light
"""
import re
from dataclasses import dataclass, field
from datetime import date

_GROUP_SPLIT_RE = re.compile(r"[,，\n;；]+")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

MAX_WECHAT_GROUPS = 10
MAX_WECHAT_CONTACTS = 10
MAX_DEFAULT_READ_DAYS = 30
DEFAULT_WORK_START = "08:00"
DEFAULT_WORK_END = "22:00"


def _clamp(value, default: int, lo: int, hi: int) -> int:
    """整数夹取（非法值回退默认）"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def parse_groups(raw) -> list[str]:
    """群列表解析：支持 list 或逗号/换行分隔字符串，去重去空，最多 10 个"""
    if isinstance(raw, list):
        items = [str(x) for x in raw]
    elif isinstance(raw, str):
        items = _GROUP_SPLIT_RE.split(raw)
    else:
        items = []

    result: list[str] = []
    for item in items:
        name = str(item).strip()
        if name and name not in result:
            result.append(name)
    return result[:MAX_WECHAT_GROUPS]


def resolve_wechat_groups(prefs: dict | None) -> list[str]:
    """微信群列表：wechat_groups → tracking_groups → default_group（新键优先）"""
    prefs = prefs or {}
    groups = parse_groups(prefs.get("wechat_groups"))
    if not groups:
        groups = parse_groups(prefs.get("tracking_groups"))
    if not groups:
        groups = parse_groups(prefs.get("default_group"))
    return groups


def resolve_wechat_contacts(prefs: dict | None) -> list[str]:
    """中介微信名列表：排除互动会话与监控群（与发送工具的安全校验同口径）"""
    from agent.config import get_settings

    prefs = prefs or {}
    contacts = parse_groups(prefs.get("wechat_contacts"))
    target = (
        str(prefs.get("wechat_target") or "").strip()
        or get_settings().wechat_sender_id.strip()
    )
    excluded = set(resolve_wechat_groups(prefs))
    if target:
        excluded.add(target)
    return [c for c in contacts if c not in excluded]


def parse_optional_minutes(value, *, lo: int = 1, hi: int = 240) -> int | None:
    """可空分钟数：None/空串/0 → None（不限制）；否则夹取到 [lo, hi]"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return max(lo, min(hi, number))


def parse_optional_hours(value) -> float | None:
    """可空小时数（每日工作时长）：非法/<=0 → None"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_hhmm(value, default: str) -> str:
    """校验 HH:MM（归一为两位小时），非法回退默认

    24:00 是滑块右端代表"当天结束"，归一为 23:59（后端合法时间）。
    """
    raw = str(value or "").strip()
    if raw in ("24:00", "24:00:00"):
        return "23:59"
    match = _HHMM_RE.match(raw)
    if not match:
        return default
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def parse_labor_level(value, legacy_prefer_light=None) -> str:
    """体力偏好：light/any/heavy；无新键时兼容旧 prefer_light"""
    level = str(value or "").strip().lower()
    if level in ("light", "any", "heavy"):
        return level
    if legacy_prefer_light is not None:
        return "light" if bool(legacy_prefer_light) else "any"
    return "any"


def resolve_default_date_range(prefs: dict | None) -> tuple[date, date]:
    """默认读取日期范围（起止含首尾）；无配置回退当天，最多 30 天"""
    prefs = prefs or {}

    def _parse(raw):
        try:
            return date.fromisoformat(str(raw).strip())
        except (TypeError, ValueError):
            return None

    raw_start = str(prefs.get("default_date_start") or prefs.get("default_date") or "").strip()
    raw_end = str(prefs.get("default_date_end") or "").strip()
    start = _parse(raw_start) if raw_start else None
    end = _parse(raw_end) if raw_end else None
    #1.配置了但格式非法 → 明确报错（空值才回退今天）
    if raw_start and start is None:
        raise ValueError(f"日期格式无法识别：{raw_start!r}")
    if raw_end and end is None:
        raise ValueError(f"日期格式无法识别：{raw_end!r}")
    if start is None:
        start = end or date.today()
    if end is None:
        end = start
    if end < start:
        raise ValueError("默认读取日期的结束日不能早于起始日")
    if (end - start).days + 1 > MAX_DEFAULT_READ_DAYS:
        raise ValueError(f"默认读取日期范围最多 {MAX_DEFAULT_READ_DAYS} 天")
    return start, end


@dataclass
class TrackingConfig:
    """跟踪器的运行时配置（每轮从 preferences 重新构建）"""

    enabled: bool = False
    interval_min: int = 15
    groups: list[str] = field(default_factory=list)
    push_enabled: bool = True
    push_limit: int = 5
    ledger_days: int = 7
    max_messages: int = 500
    push_lookback_min: int = 1440
    commute_origin: str = ""
    amap_city: str = ""
    max_commute_min: int | None = None
    max_commute_transit_min: int | None = None
    max_commute_drive_min: int | None = None
    commission_min: float | None = None
    labor_level: str = "any"
    prefer_light: bool = False
    wechat_access_enabled: bool = True

    @property
    def paused_reason(self) -> str:
        """未启用/未配置群时的暂停原因（状态接口展示）"""
        if not self.wechat_access_enabled:
            return "微信访问已关闭"
        if not self.enabled:
            return "跟踪未启用"
        if not self.groups:
            return "未配置跟踪群"
        return ""


@dataclass
class InteractionConfig:
    """微信互动配置（轮询模式；微信 MCP 无监听接口）"""

    enabled: bool = False
    interval_sec: int = 60
    target: str = ""              # 互动会话名；空则回退 .env 的 WECHAT_SENDER_ID
    max_replies_per_hour: int = 30
    pending_ttl_min: int = 1440
    wechat_access_enabled: bool = True

    @property
    def paused_reason(self) -> str:
        if not self.wechat_access_enabled:
            return "微信访问已关闭"
        if not self.enabled:
            return "互动未启用"
        if not self.target:
            return "未配置微信目标"
        return ""


def load_interaction_config(prefs: dict | None) -> InteractionConfig:
    """从 preferences 构建互动配置（间隔夹取 30~300 秒，目标回退 .env）"""
    prefs = prefs or {}
    from agent.config import get_settings

    target = (
        str(prefs.get("wechat_target") or "").strip()
        or get_settings().wechat_sender_id.strip()
    )
    return InteractionConfig(
        enabled=bool(prefs.get("interaction_enabled", False)),
        interval_sec=_clamp(prefs.get("interaction_interval_sec"), 60, 30, 300),
        target=target,
        max_replies_per_hour=_clamp(prefs.get("interaction_max_replies_per_hour"), 30, 1, 120),
        pending_ttl_min=_clamp(prefs.get("interaction_pending_ttl_min"), 1440, 60, 10080),
        wechat_access_enabled=bool(prefs.get("wechat_access_enabled", True)),
    )


@dataclass
class ContactInteractionConfig:
    """中介互动配置（后台轮询中介会话，草拟回复经用户确认后发送）"""

    enabled: bool = False
    interval_sec: int = 60
    contacts: list[str] = field(default_factory=list)
    wechat_access_enabled: bool = True

    @property
    def paused_reason(self) -> str:
        if not self.wechat_access_enabled:
            return "微信访问已关闭"
        if not self.enabled:
            return "中介互动未启用"
        if not self.contacts:
            return "未配置中介名单"
        return ""


def load_contact_interaction_config(prefs: dict | None) -> ContactInteractionConfig:
    """从 preferences 构建中介互动配置（间隔夹取 30~300 秒）"""
    prefs = prefs or {}
    return ContactInteractionConfig(
        enabled=bool(prefs.get("contact_interaction_enabled", False)),
        interval_sec=_clamp(prefs.get("contact_interaction_interval_sec"), 60, 30, 300),
        contacts=resolve_wechat_contacts(prefs),
        wechat_access_enabled=bool(prefs.get("wechat_access_enabled", True)),
    )


def load_chat_preferences(prefs: dict | None):
    """聊天用显式偏好（供 system prompt 动态注入）"""
    from types import SimpleNamespace

    prefs = prefs or {}
    commission_min = prefs.get("commission_min", 100)
    try:
        commission_min = float(commission_min)
    except (TypeError, ValueError):
        commission_min = 100.0
    labor_level = parse_labor_level(prefs.get("labor_level"), prefs.get("prefer_light"))
    gender = str(prefs.get("gender") or "any").strip().lower()
    if gender not in ("male", "female"):
        gender = "any"
    try:
        age = int(prefs.get("age")) if prefs.get("age") not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    if age is not None and not (16 <= age <= 80):
        age = None
    return SimpleNamespace(
        gender=gender,
        age=age,
        commission_min=commission_min,
        max_commute_min=parse_optional_minutes(prefs.get("max_commute_min")),
        max_commute_transit_min=parse_optional_minutes(prefs.get("max_commute_transit_min")),
        max_commute_drive_min=parse_optional_minutes(prefs.get("max_commute_drive_min")),
        labor_level=labor_level,
        prefer_light=labor_level == "light",
        daily_hours_max=parse_optional_hours(prefs.get("daily_hours_max")),
        work_time_start=parse_hhmm(prefs.get("work_time_start"), DEFAULT_WORK_START),
        work_time_end=parse_hhmm(prefs.get("work_time_end"), DEFAULT_WORK_END),
        note=str(prefs.get("note") or "").strip(),
        commute_origin=str(prefs.get("commute_origin") or "").strip(),
        wechat_contacts=resolve_wechat_contacts(prefs),
        wechat_access_enabled=bool(prefs.get("wechat_access_enabled", True)),
    )


def load_tracking_config(prefs: dict | None) -> TrackingConfig:
    """从 preferences 构建跟踪配置（全部夹取到安全范围）"""
    prefs = prefs or {}
    commission_min = prefs.get("commission_min")
    try:
        commission_min = float(commission_min) if commission_min is not None else None
    except (TypeError, ValueError):
        commission_min = None

    labor_level = parse_labor_level(prefs.get("labor_level"), prefs.get("prefer_light"))
    return TrackingConfig(
        enabled=bool(prefs.get("tracking_enabled", False)),
        interval_min=_clamp(prefs.get("tracking_interval_min"), 15, 5, 180),
        groups=resolve_wechat_groups(prefs),
        push_enabled=bool(prefs.get("tracking_push_enabled", True)),
        push_limit=_clamp(prefs.get("tracking_push_limit"), 5, 1, 10),
        ledger_days=_clamp(prefs.get("tracking_ledger_days"), 7, 1, 30),
        max_messages=_clamp(prefs.get("tracking_max_messages"), 500, 50, 1000),
        push_lookback_min=_clamp(prefs.get("tracking_push_lookback_min"), 1440, 60, 1440),
        commute_origin=str(prefs.get("commute_origin") or "").strip(),
        amap_city=str(prefs.get("amap_city") or "").strip(),
        max_commute_min=parse_optional_minutes(prefs.get("max_commute_min")),
        max_commute_transit_min=parse_optional_minutes(prefs.get("max_commute_transit_min")),
        max_commute_drive_min=parse_optional_minutes(prefs.get("max_commute_drive_min")),
        commission_min=commission_min,
        labor_level=labor_level,
        prefer_light=labor_level == "light",
        wechat_access_enabled=bool(prefs.get("wechat_access_enabled", True)),
    )