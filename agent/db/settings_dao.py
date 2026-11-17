# -*- coding: utf-8 -*-
"""
app_settings 单行 JSONB 读写与配置合并（DB 覆盖值优先于 .env）。

DB 结构（嵌套）：
    {"strong": {"model": "...", "api_key": "...", "base_url": "..."},
     "weak": {...}, "backup": {...},
     "embedding": {"model": "...", "api_key": "...", "base_url": "...", "dims": 1024},
     "preferences": {...自由 JSON...}}
"""
import copy

from loguru import logger
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from agent.config import Settings

#1.白名单：可被设置页覆盖的模型档位与字段（未知字段一律拒绝）
MODEL_KINDS = ("strong", "weak", "backup", "embedding")
MODEL_FIELDS = ("model", "api_key", "base_url")
EXTRA_FIELDS = {"embedding": ("dims",)}
AMAP_FIELDS = ("api_key", "url")
TOP_KEYS = set(MODEL_KINDS) | {"preferences", "amap"}


async def get_raw_settings(pool: AsyncConnectionPool) -> dict:
    """读取 app_settings 中的覆盖数据（无记录返回空字典）"""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT data FROM app_settings WHERE id = 1")
        row = await cur.fetchone()
    return dict(row[0]) if row and row[0] else {}


async def save_raw_settings(pool: AsyncConnectionPool, data: dict) -> None:
    """整体写入覆盖数据（upsert 单行）"""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO app_settings (id, data, updated_at) VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (Jsonb(data),),
        )
    # 只记录字段名，绝不记录含密钥的值
    logger.info("app_settings 已保存，顶层字段：{}", sorted(data.keys()))


async def reset_default_read_dates(pool: AsyncConnectionPool, day: str) -> None:
    """启动时把默认读取日期重置为当天（只在当次运行有效，重启即滚动到今日）

    只更新 preferences 的三个日期键，保留模型块与其他偏好。

    Args:
        day: ISO 日期字符串（如 "2026-11-09"）
    """
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings AS t (id, data, updated_at)
            VALUES (
                1,
                jsonb_build_object(
                    'preferences',
                    jsonb_build_object(
                        'default_date_start', %(day)s::text,
                        'default_date_end', %(day)s::text,
                        'default_date', %(day)s::text
                    )
                ),
                now()
            )
            ON CONFLICT (id) DO UPDATE SET
                data = jsonb_set(
                    COALESCE(t.data, '{}'::jsonb),
                    '{preferences}',
                    COALESCE(t.data->'preferences', '{}'::jsonb) || jsonb_build_object(
                        'default_date_start', %(day)s::text,
                        'default_date_end', %(day)s::text,
                        'default_date', %(day)s::text
                    ),
                    true
                ),
                updated_at = now()
            """,
            {"day": day},
        )
    logger.info("默认读取日期已重置为 {}", day)


async def add_wechat_contact(
    pool: AsyncConnectionPool, contact: str, *, max_items: int = 10
) -> bool:
    """原子追加中介微信名到 preferences.wechat_contacts（去重、限长、保留其他键）

    单条 UPDATE 内完成读改写，避免与设置页 PUT 的整行覆盖竞争。

    Returns:
        写入后名单中是否包含该联系人（空名/失败返回 False）
    """
    name = str(contact or "").strip()
    if not name:
        return False
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO app_settings AS t (id, data, updated_at)
                VALUES (
                    1,
                    jsonb_build_object(
                        'preferences',
                        jsonb_build_object('wechat_contacts', jsonb_build_array(%(name)s::text))
                    ),
                    now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    data = jsonb_set(
                        t.data,
                        '{preferences}',
                        jsonb_set(
                            COALESCE(t.data->'preferences', '{}'::jsonb),
                            '{wechat_contacts}',
                            CASE
                                WHEN EXISTS (
                                    SELECT 1
                                    FROM jsonb_array_elements_text(
                                        COALESCE(t.data->'preferences'->'wechat_contacts', '[]'::jsonb)
                                    ) AS e(v)
                                    WHERE e.v = %(name)s::text
                                ) THEN COALESCE(t.data->'preferences'->'wechat_contacts', '[]'::jsonb)
                                WHEN jsonb_array_length(
                                    COALESCE(t.data->'preferences'->'wechat_contacts', '[]'::jsonb)
                                ) >= %(max)s THEN COALESCE(t.data->'preferences'->'wechat_contacts', '[]'::jsonb)
                                ELSE COALESCE(t.data->'preferences'->'wechat_contacts', '[]'::jsonb)
                                     || jsonb_build_array(%(name)s::text)
                            END,
                            true
                        ),
                        true
                    ),
                    updated_at = now()
                RETURNING data->'preferences' AS prefs
                """,
                {"name": name, "max": max_items},
            )
            row = await cur.fetchone()
        prefs = dict(row[0]) if row and row[0] else {}
        #5.同步进程内单例，下一次模型调用即可在提示词中看到新名单
        from agent.config import get_settings

        get_settings().preferences = prefs
        contacts = [str(x) for x in (prefs.get("wechat_contacts") or [])]
        return name in contacts
    except Exception as exc:  # 追加失败不影响发送结果
        logger.warning("中介名单自动追加失败 contact={}：{}", name, exc)
        return False


def _validate_block(kind: str, block: dict) -> None:
    """校验单个模型块/高德块的字段名（未知字段抛 ValueError）"""
    if kind == "amap":
        allowed = set(AMAP_FIELDS)
    else:
        allowed = set(MODEL_FIELDS) | set(EXTRA_FIELDS.get(kind, ()))
    unknown = set(block.keys()) - allowed
    if unknown:
        raise ValueError(f"{kind} 含未知字段：{sorted(unknown)}")


def apply_overrides(settings: Settings, data: dict) -> list[str]:
    """把 DB 嵌套覆盖应用到 Settings 单例字段

    Args:
        settings: get_settings() 返回的单例（直接修改）
        data: app_settings.data（嵌套结构）

    Returns:
        实际应用的字段名列表（如 ["strong_model", "embedding_dims"]）

    Raises:
        ValueError: 未知顶层键或块内未知字段
    """
    unknown_top = set(data.keys()) - TOP_KEYS
    if unknown_top:
        raise ValueError(f"未知设置项：{sorted(unknown_top)}")

    applied: list[str] = []
    for kind in MODEL_KINDS:
        block = data.get(kind)
        if not isinstance(block, dict):
            continue
        _validate_block(kind, block)
        for field in MODEL_FIELDS:
            if field in block:
                attr = f"{kind}_{field}"
                setattr(settings, attr, block[field])
                applied.append(attr)
        if "dims" in block:
            setattr(settings, "embedding_dims", int(block["dims"]))
            applied.append("embedding_dims")

    #偏好设置整体替换
    if isinstance(data.get("preferences"), dict):
        setattr(settings, "preferences", dict(data["preferences"]))
        applied.append("preferences")

    #4.高德 MCP：url 优先（Remote），key 优先用 DB（覆盖 .env），空则回退 .env
    amap = data.get("amap")
    if isinstance(amap, dict):
        _validate_block("amap", amap)
        if "api_key" in amap:
            # DB 中保存的 key 必须同时覆盖 amap_server_key（所有 REST/MCP 调用方读它）
            setattr(settings, "amap_maps_api_key", amap["api_key"])
            setattr(settings, "amap_server_key", amap["api_key"])
            applied.append("amap_maps_api_key")
        if "url" in amap:
            setattr(settings, "amap_url", amap["url"])
            applied.append("amap_url")
    return applied


def merge_patch(current: dict, patch: dict) -> dict:
    """合并 PUT 补丁与现有覆盖（PUT 语义：空串=不修改、None=清除、顶层 null=清整块）

    Args:
        current: 现有 app_settings.data
        patch: 请求中的 patch（结构同上）

    Returns:
        合并后的新 data（不修改入参）
    """
    unknown_top = set(patch.keys()) - TOP_KEYS
    if unknown_top:
        raise ValueError(f"未知设置项：{sorted(unknown_top)}")

    merged = copy.deepcopy(current)
    for key, value in patch.items():
        #2.顶层 null：清除整个块（回退 .env）
        if value is None:
            merged.pop(key, None)
            continue
        #3.preferences 自由 JSON：整体替换
        if key == "preferences":
            merged["preferences"] = value
            continue
        #4.模型块：逐字段合并
        if not isinstance(value, dict):
            raise ValueError(f"{key} 必须是对象或 null")
        _validate_block(key, value)
        block = dict(merged.get(key) or {})
        for field, field_value in value.items():
            if field_value is None:
                block.pop(field, None)      # 显式 null：清除该覆盖
            elif field_value == "":
                continue                    # 空串：不修改
            else:
                block[field] = field_value
        if block:
            merged[key] = block
        else:
            merged.pop(key, None)
    return merged


def validate_effective_settings(settings: Settings) -> None:
    """构建模型前校验生效配置（strong/weak 必须完整；backup 允许整体为空）

    Raises:
        ValueError: 配置不完整（消息含档位名，不进日志）
    """
    for kind in ("strong", "weak"):
        missing = [
            f"{kind}.{field}"
            for field in MODEL_FIELDS
            if not getattr(settings, f"{kind}_{field}", "")
        ]
        if missing:
            raise ValueError(f"模型配置不完整，缺少：{', '.join(missing)}")

    #5.备用模型：三项必须同时存在或同时为空
    backup_values = [getattr(settings, f"backup_{f}", "") for f in MODEL_FIELDS]
    if any(backup_values) and not all(backup_values):
        raise ValueError("备用模型配置不完整：model/api_key/base_url 需同时填写或全部留空")

    if settings.embedding_dims <= 0:
        raise ValueError("embedding_dims 必须为正整数")


def mask_settings(settings: Settings) -> dict:
    """生成 GET /api/settings 响应（API key 只返回是否已配置，绝不返回明文）"""
    result: dict = {}
    for kind in MODEL_KINDS:
        result[kind] = {
            "model": getattr(settings, f"{kind}_model", ""),
            "base_url": getattr(settings, f"{kind}_base_url", ""),
            "api_key_configured": bool(getattr(settings, f"{kind}_api_key", "")),
        }
    result["embedding"]["dims"] = settings.embedding_dims
    result["amap"] = {
        # .env 可能只配置 AMAP_SERVER_KEY（标准后端变量），两者任一有值都算已配置
        "api_key_configured": bool(settings.amap_server_key or settings.amap_maps_api_key),
        "url": settings.amap_url,
    }
    result["preferences"] = dict(settings.preferences or {})
    return result