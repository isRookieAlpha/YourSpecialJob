# -*- coding: utf-8 -*-
"""微信摄入路由：POST /api/wechat/ingest（前端按钮直调通道，与 agent 工具共用管道）。"""
from datetime import date

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from agent.config import get_settings
from agent.ingest import ingest_group
from agent.mcp_wechat import to_mcp_date
from agent.models.job import IngestStats

router = APIRouter(prefix="/api/wechat", tags=["wechat"])


class IngestRequest(BaseModel):
    """摄入请求：前端传 ISO 日期，后端转 MCP 的 YY/M/D"""

    group_name: str = Field(description="微信群名，如 兼职1群")
    target_date: date = Field(description="目标日期（YYYY-MM-DD）")


@router.post("/ingest", response_model=IngestStats)
async def wechat_ingest(body: IngestRequest):
    """读取群消息 → 结构化 → 入库（长耗时接口，执行期间请勿操作微信）"""
    prefs = get_settings().preferences or {}
    if not bool(prefs.get("wechat_access_enabled", True)):
        raise HTTPException(403, detail="微信访问已在设置页关闭")
    try:
        return await ingest_group(body.group_name, to_mcp_date(body.target_date))
    except Exception as e:
        logger.exception("微信摄入失败")
        raise HTTPException(502, detail=str(e))