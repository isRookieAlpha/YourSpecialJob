# -*- coding: utf-8 -*-
"""设置路由：GET/PUT /api/settings（DB 覆盖值优先于 .env）+ POST /api/settings/test。"""
import time

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from agent.config import get_settings
from agent.db.settings_dao import (
    apply_overrides,
    get_raw_settings,
    mask_settings,
    merge_patch,
    save_raw_settings,
    validate_effective_settings,
)
from agent.models_factory import get_llm_backup, get_llm_strong, get_llm_weak, reset_models
from backend.deps import get_pool, rebuild_agent

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPatch(BaseModel):
    """设置补丁（未出现的顶层键不处理；null 清除整块；空串不修改字段）"""

    strong: dict | None = Field(default=None, description="强模型块")
    weak: dict | None = Field(default=None, description="弱模型块")
    backup: dict | None = Field(default=None, description="备用模型块")
    embedding: dict | None = Field(default=None, description="embedding 块")
    amap: dict | None = Field(default=None, description="高德 MCP 块（api_key / url）")
    preferences: dict | None = Field(default=None, description="偏好设置（自由 JSON）")


class ModelTestRequest(BaseModel):
    """模型连通性测试"""

    kind: str = Field(description="strong / weak / backup")


def _restore_settings(data: dict) -> None:
    """把 Settings 单例恢复到指定覆盖状态（失败回滚用）"""
    get_settings.cache_clear()
    apply_overrides(get_settings(), data)
    reset_models()


@router.get("")
async def read_settings(request: Request):
    """当前生效配置（API key 只返回是否已配置）"""
    return mask_settings(get_settings())


@router.put("")
async def update_settings(body: SettingsPatch, request: Request):
    """保存设置并重建 agent（校验失败 422，构建失败 500，均不影响现有实例）"""
    pool = get_pool(request)
    current_data = await get_raw_settings(pool)
    patch = body.model_dump(exclude_unset=True)

    #1.合并补丁（未知键/未知字段在这里被拒绝）
    try:
        merged = merge_patch(current_data, patch)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

    #2.重建 Settings 单例：回到 .env 基线 → 应用合并后的覆盖 → 校验
    get_settings.cache_clear()
    settings = get_settings()
    try:
        apply_overrides(settings, merged)
        validate_effective_settings(settings)
    except ValueError as e:
        _restore_settings(current_data)  # 回滚到旧覆盖
        raise HTTPException(422, detail=str(e))

    #3.仅偏好/高德 MCP 变化时无需重建 agent（只有模型块参与 agent 装配）
    model_keys = {"strong", "weak", "backup", "embedding"}
    model_changed = (
        {k: v for k, v in merged.items() if k in model_keys}
        != {k: v for k, v in current_data.items() if k in model_keys}
    )
    reset_models()
    if model_changed:
        try:
            await rebuild_agent(request.app)
        except Exception:
            _restore_settings(current_data)
            raise HTTPException(500, detail="重建 agent 失败，已回滚到原配置（详情见日志）")

    #4.构建成功后才持久化
    await save_raw_settings(pool, merged)
    return {
        "saved": True,
        "agent_rebuilt": model_changed,
        "settings": mask_settings(get_settings()),
    }


@router.post("/test")
async def test_model(body: ModelTestRequest):
    """用对应档位模型发一条最短消息验证连通性"""
    factories = {"strong": get_llm_strong, "weak": get_llm_weak, "backup": get_llm_backup}
    if body.kind not in factories:
        raise HTTPException(400, detail="kind 必须是 strong / weak / backup")
    llm = factories[body.kind]()
    if llm is None:
        raise HTTPException(400, detail=f"{body.kind} 模型未配置")

    t0 = time.time()
    try:
        await llm.ainvoke("你好，请只回复两个字：正常")
        return {"kind": body.kind, "ok": True, "elapsed_sec": round(time.time() - t0, 1)}
    except Exception as e:
        logger.exception("模型连通性测试失败 kind={}", body.kind)
        return {
            "kind": body.kind,
            "ok": False,
            "elapsed_sec": round(time.time() - t0, 1),
            "error": f"{type(e).__name__}: {e}",
        }