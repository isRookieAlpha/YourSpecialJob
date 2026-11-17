# -*- coding: utf-8 -*-
"""心动的offer 输入模型（手动录入 / 编辑 / 状态流转的 Pydantic 校验）。"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

OfferStatus = Literal["none", "favorite", "todo"]
CommissionUnit = Literal["元/时", "元/天", "元/次", "元/月"]
PhysicalIntensity = Literal["轻", "中", "重"]


class ManualOfferInput(BaseModel):
    """手动录入兼职（不经大模型，直接写 jobs 表）"""

    work_summary: str = Field(min_length=1, max_length=200, description="工作内容摘要（必填）")
    work_date: date = Field(description="工作日期（必填）")
    work_time: str | None = Field(default=None, description="工作时段，如 7:30-21:00")
    commission_min: float | None = Field(default=None, ge=0, description="佣金下限")
    commission_max: float | None = Field(default=None, ge=0, description="佣金上限")
    commission_unit: CommissionUnit | None = Field(default=None, description="佣金单位")
    location: str | None = Field(default=None, max_length=200, description="工作地点")
    contact: str | None = Field(default=None, max_length=200, description="联系方式")
    notes: str | None = Field(default=None, max_length=1000, description="备注")
    physical_intensity: PhysicalIntensity | None = Field(default=None, description="体力强度")
    is_todo: bool = Field(default=False, description="是否同时设为 ToDo 待办offer")

    @model_validator(mode="after")
    def _check_commission_range(self):
        if (
            self.commission_min is not None
            and self.commission_max is not None
            and self.commission_min > self.commission_max
        ):
            raise ValueError("佣金下限不能大于上限")
        return self


class OfferUpdateInput(BaseModel):
    """编辑兼职（白名单字段；禁止改 source/raw_text/offer_status 等血缘字段）"""

    work_summary: str | None = Field(default=None, min_length=1, max_length=200)
    work_date: date | None = None
    work_time: str | None = None
    commission_min: float | None = Field(default=None, ge=0)
    commission_max: float | None = Field(default=None, ge=0)
    commission_unit: CommissionUnit | None = None
    location: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)
    physical_intensity: PhysicalIntensity | None = None

    @model_validator(mode="after")
    def _check_commission_range(self):
        if (
            self.commission_min is not None
            and self.commission_max is not None
            and self.commission_min > self.commission_max
        ):
            raise ValueError("佣金下限不能大于上限")
        return self


class OfferStatusPatch(BaseModel):
    """状态流转（升级/降级/取消收藏）

    mode=set：无条件写目标状态（统计页升降级用）
    mode=promote：只允许升级（favorite 仅 none→favorite；todo 仅 none/favorite→todo）
    """

    job_ids: list[int] = Field(min_length=1, description="兼职 id 列表")
    status: OfferStatus = Field(description="目标状态")
    mode: Literal["set", "promote"] = Field(default="set", description="set / promote")