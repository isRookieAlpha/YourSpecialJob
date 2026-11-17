# -*- coding: utf-8 -*-
"""长期记忆条目模型（弱模型结构化提取 + Store 存储格式）。"""
from typing import Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["preference", "constraint", "fact"]


class MemoryItem(BaseModel):
    """一条长期记忆"""

    kind: MemoryKind = Field(
        description="preference=偏好（如日结优先）；constraint=约束（如不接夜班）；fact=用户自述事实"
    )
    text: str = Field(description="一句话记忆（中文，简洁，包含关键信息）")
    confidence: float = Field(default=0.8, ge=0, le=1, description="置信度 0~1")


class MemoryBatch(BaseModel):
    """一轮对话提取出的记忆列表（无新记忆时为空列表）"""

    items: list[MemoryItem] = Field(default_factory=list, description="新提取的记忆条目")