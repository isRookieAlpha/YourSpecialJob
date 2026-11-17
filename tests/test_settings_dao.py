# -*- coding: utf-8 -*-
"""settings_dao 测试：覆盖合并 / PUT 语义 / 校验 / 脱敏 / DB 往返。"""
import pytest

from agent.config import Settings
from agent.db.settings_dao import (
    apply_overrides,
    get_raw_settings,
    mask_settings,
    merge_patch,
    save_raw_settings,
    validate_effective_settings,
)


def empty_settings() -> Settings:
    """确定性空配置（忽略本机 agent/.env）"""
    return Settings(_env_file=None)


def test_apply_overrides_maps_nested_fields():
    """嵌套块映射到 Settings 字段"""
    s = empty_settings()
    applied = apply_overrides(s, {
        "strong": {"model": "m1", "api_key": "k1", "base_url": "u1"},
        "embedding": {"model": "e1", "dims": 512},
        "preferences": {"commission_min": 100},
    })
    assert s.strong_model == "m1" and s.strong_api_key == "k1"
    assert s.embedding_model == "e1" and s.embedding_dims == 512
    assert s.preferences == {"commission_min": 100}
    assert "strong_model" in applied and "embedding_dims" in applied


def test_apply_overrides_rejects_unknown_top_key():
    """未知顶层键被拒绝"""
    with pytest.raises(ValueError, match="未知设置项"):
        apply_overrides(empty_settings(), {"foo": {}})


def test_apply_overrides_rejects_unknown_block_field():
    """块内未知字段被拒绝"""
    with pytest.raises(ValueError, match="未知字段"):
        apply_overrides(empty_settings(), {"strong": {"apiKey": "x"}})


def test_merge_patch_empty_string_keeps_value():
    """PUT 语义：空串 = 不修改"""
    merged = merge_patch(
        {"weak": {"model": "old", "api_key": "k"}},
        {"weak": {"model": "new", "api_key": ""}},
    )
    assert merged["weak"]["model"] == "new"
    assert merged["weak"]["api_key"] == "k"


def test_merge_patch_null_clears_field_and_block():
    """PUT 语义：字段 null 清除；顶层 null 清整块"""
    merged = merge_patch(
        {"weak": {"model": "m", "api_key": "k"}, "backup": {"model": "b"}},
        {"weak": {"api_key": None}, "backup": None},
    )
    assert merged["weak"] == {"model": "m"}
    assert "backup" not in merged


def test_merge_patch_replaces_preferences():
    """preferences 整体替换"""
    merged = merge_patch({"preferences": {"a": 1}}, {"preferences": {"b": 2}})
    assert merged["preferences"] == {"b": 2}


def test_validate_requires_strong_and_weak():
    """strong/weak 必须完整"""
    s = empty_settings()
    with pytest.raises(ValueError, match="strong"):
        validate_effective_settings(s)


def test_validate_backup_all_empty_or_all_filled():
    """备用模型：全空通过；部分填写报错"""
    s = empty_settings()
    s.strong_model, s.strong_api_key, s.strong_base_url = "m", "k", "u"
    s.weak_model, s.weak_api_key, s.weak_base_url = "m", "k", "u"
    validate_effective_settings(s)  # 不抛错

    s.backup_model = "b"
    with pytest.raises(ValueError, match="备用模型"):
        validate_effective_settings(s)


def test_validate_rejects_bad_dims():
    """embedding_dims 必须为正"""
    s = empty_settings()
    s.strong_model, s.strong_api_key, s.strong_base_url = "m", "k", "u"
    s.weak_model, s.weak_api_key, s.weak_base_url = "m", "k", "u"
    s.embedding_dims = 0
    with pytest.raises(ValueError, match="embedding_dims"):
        validate_effective_settings(s)


def test_mask_settings_hides_api_keys():
    """GET 响应不含明文 key，只返回 configured"""
    s = empty_settings()
    s.strong_model, s.strong_api_key, s.strong_base_url = "m", "secret-key", "u"
    s.preferences = {"x": 1}
    masked = mask_settings(s)
    text = str(masked)
    assert "secret-key" not in text
    assert masked["strong"]["api_key_configured"] is True
    assert masked["weak"]["api_key_configured"] is False
    assert masked["preferences"] == {"x": 1}


async def test_save_and_get_roundtrip(pool):
    """DB 写入/读回"""
    assert await get_raw_settings(pool) == {}
    await save_raw_settings(pool, {"weak": {"model": "m2"}})
    assert await get_raw_settings(pool) == {"weak": {"model": "m2"}}
    # 二次写入整体覆盖
    await save_raw_settings(pool, {"strong": {"model": "m1"}})
    assert await get_raw_settings(pool) == {"strong": {"model": "m1"}}

def test_apply_overrides_amap_block():
    """高德 MCP：api_key/url 映射；DB key 必须覆盖 .env 的 amap_server_key"""
    s = empty_settings()
    s.amap_server_key = "env-old-key"  # 模拟 .env 已配置旧 key
    applied = apply_overrides(s, {"amap": {"api_key": "amap-key", "url": "https://x/mcp"}})
    assert s.amap_maps_api_key == "amap-key"
    assert s.amap_url == "https://x/mcp"
    assert s.amap_server_key == "amap-key"  # DB 优先，旧 key 被覆盖
    assert "amap_url" in applied


def test_apply_overrides_amap_rejects_unknown_field():
    with pytest.raises(ValueError, match="未知字段"):
        apply_overrides(empty_settings(), {"amap": {"key": "x"}})


def test_merge_patch_amap_semantics():
    """amap 块遵循同一 PUT 语义：空串不修改、null 清除"""
    merged = merge_patch(
        {"amap": {"api_key": "k", "url": "https://old/mcp"}},
        {"amap": {"api_key": "", "url": None}},
    )
    assert merged["amap"] == {"api_key": "k"}


def test_mask_settings_amap():
    """GET 响应不回显高德 key，只返回是否已配置与 URL"""
    s = empty_settings()
    s.amap_maps_api_key = "amap-secret"
    s.amap_url = "https://x/mcp"
    masked = mask_settings(s)
    assert "amap-secret" not in str(masked)
    assert masked["amap"] == {"api_key_configured": True, "url": "https://x/mcp"}

    # .env 只配置 AMAP_SERVER_KEY 时也应显示已配置
    s2 = empty_settings()
    s2.amap_server_key = "server-key"
    assert mask_settings(s2)["amap"]["api_key_configured"] is True


async def test_reset_default_read_dates_overwrites_and_preserves_keys(pool):
    """启动重置：日期改为当天，模型块与其他偏好保留"""
    from agent.db.settings_dao import reset_default_read_dates

    await save_raw_settings(pool, {
        "strong": {"model": "m1", "api_key": "k1", "base_url": "u1"},
        "preferences": {
            "commission_min": 120,
            "default_date_start": "2026-11-07",
            "default_date_end": "2026-11-07",
            "default_date": "2026-11-07",
        },
    })
    await reset_default_read_dates(pool, "2026-11-09")

    data = await get_raw_settings(pool)
    prefs = data["preferences"]
    assert prefs["default_date_start"] == "2026-11-09"
    assert prefs["default_date_end"] == "2026-11-09"
    assert prefs["default_date"] == "2026-11-09"
    assert prefs["commission_min"] == 120
    assert data["strong"]["model"] == "m1"


async def test_reset_default_read_dates_creates_row(pool):
    """无 app_settings 行时创建只含日期的记录"""
    from agent.db.settings_dao import reset_default_read_dates

    assert await get_raw_settings(pool) == {}
    await reset_default_read_dates(pool, "2026-11-09")
    data = await get_raw_settings(pool)
    assert data["preferences"]["default_date_start"] == "2026-11-09"


async def test_add_wechat_contact_atomic_append(pool, monkeypatch):
    """原子追加中介名单：去重、保留其他 preferences 键、同步进程内单例"""
    from types import SimpleNamespace

    from agent.db.settings_dao import add_wechat_contact

    fake = SimpleNamespace(preferences={})
    monkeypatch.setattr("agent.config.get_settings", lambda: fake)

    assert await add_wechat_contact(pool, "小王") is True
    assert await add_wechat_contact(pool, "小王") is True  # 已存在：不重复
    assert await add_wechat_contact(pool, " 张姐 ") is True

    data = await get_raw_settings(pool)
    assert data["preferences"]["wechat_contacts"] == ["小王", "张姐"]
    assert fake.preferences["wechat_contacts"] == ["小王", "张姐"]


async def test_add_wechat_contact_preserves_model_blocks(pool, monkeypatch):
    """追加名单不覆盖模型块与其他 preferences 键"""
    from types import SimpleNamespace

    from agent.db.settings_dao import add_wechat_contact

    monkeypatch.setattr(
        "agent.config.get_settings", lambda: SimpleNamespace(preferences={})
    )
    await save_raw_settings(pool, {
        "strong": {"model": "m1", "api_key": "k1", "base_url": "u1"},
        "preferences": {"commission_min": 120, "wechat_contacts": ["老中介"]},
    })
    await add_wechat_contact(pool, "小王")

    data = await get_raw_settings(pool)
    assert data["strong"]["model"] == "m1"
    assert data["preferences"]["commission_min"] == 120
    assert data["preferences"]["wechat_contacts"] == ["老中介", "小王"]


async def test_add_wechat_contact_caps_at_ten(pool, monkeypatch):
    """超过上限不再追加（保留原名单）"""
    from types import SimpleNamespace

    from agent.db.settings_dao import add_wechat_contact

    monkeypatch.setattr(
        "agent.config.get_settings", lambda: SimpleNamespace(preferences={})
    )
    names = [f"中介{i}" for i in range(10)]
    await save_raw_settings(pool, {"preferences": {"wechat_contacts": names}})
    await add_wechat_contact(pool, "第11个")

    data = await get_raw_settings(pool)
    assert data["preferences"]["wechat_contacts"] == names


async def test_add_wechat_contact_empty_name(pool, monkeypatch):
    from types import SimpleNamespace

    from agent.db.settings_dao import add_wechat_contact

    monkeypatch.setattr(
        "agent.config.get_settings", lambda: SimpleNamespace(preferences={})
    )
    assert await add_wechat_contact(pool, "  ") is False
    assert await get_raw_settings(pool) == {}
