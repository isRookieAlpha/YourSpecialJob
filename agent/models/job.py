# -*- coding: utf-8 -*-
"""
兼职信息 Pydantic 模型：弱模型结构化输出的 schema 与入库统计。

字段与 jobs 表一一对应（M1 阶段全部可空，缺省值由 DAO/SQL 填充）。
raw_text 血缘回填是 content_hash 去重的根基，提取提示词必须要求逐字符一致。
"""
import hashlib
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

#1.体力强度与佣金单位的受限取值（与 schema.sql 的 CHECK 约束一致）
PhysicalIntensity = Literal["轻", "中", "重"]
CommissionUnit = Literal["元/时", "元/天", "元/次", "元/月"]


class JobMessage(BaseModel):
    """一条结构化兼职信息"""

    work_summary: str | None = Field(default=None, description="工作内容摘要，如'酒店传菜'")
    is_physical_labor: bool | None = Field(default=None, description="是否体力活")
    physical_intensity: PhysicalIntensity | None = Field(default=None, description="体力强度：轻/中/重")
    work_date: date | None = Field(default=None, description="工作日期（消息文本中的日期优先，缺省回填查询日期）")
    work_time: str | None = Field(default=None, description="工作时段，如'7:30-21:00'")
    publish_time: datetime | None = Field(default=None, description="消息发布时间（消息时刻+查询日期合成）")
    commission_text: str | None = Field(default=None, description="佣金原文表述，保血缘，如'日结110'")
    commission_min: float | None = Field(default=None, description="佣金下限（'100左右'→90）")
    commission_max: float | None = Field(default=None, description="佣金上限（'100左右'→110）")
    commission_unit: CommissionUnit | None = Field(default=None, description="佣金单位")
    location: str | None = Field(default=None, description="工作地点描述")
    deposit: float | None = Field(default=None, description="押金金额（无押金为 None）")
    travel_cost: float | None = Field(default=None, description="路费补贴")
    is_daily_settlement: bool | None = Field(default=None, description="是否日结")
    settlement_method: str | None = Field(default=None, description="结算方式描述")
    contact: str | None = Field(default=None, description="报名联系方式，如'加微信xxx'")
    publisher: str | None = Field(default=None, description="发布者（群内显示名）")
    notes: str | None = Field(default=None, description="其他要求备注（性别年龄限制等）")


class JobExtractItem(BaseModel):
    """单条消息的提取结果"""

    source_index: int | None = Field(default=None, description="该条消息在输入列表中的行首 [序号]（血缘定位用）")
    raw_text: str = Field(description="该条消息原文（逐字符一致回填，不要改写/截断/总结）")
    is_valid_job: bool = Field(description="是否为有效兼职招聘信息（表情包/闲聊/求助判 False）")
    job: JobMessage | None = Field(default=None, description="兼职结构化信息（is_valid_job=True 时必填）")


class ExtractedBatch(BaseModel):
    """一批消息的结构化提取结果"""

    items: list[JobExtractItem] = Field(default_factory=list, description="本批逐条提取结果，无有效兼职时可为空列表")


class IngestStats(BaseModel):
    """一次群+日期摄入的统计摘要"""

    group_name: str = Field(description="群名")
    target_date: str = Field(description="目标日期（YY/M/D 格式）")
    source_ref: str = Field(description="导出 json 文件名")
    total_messages: int = Field(description="原始消息总数")
    noise_filtered: int = Field(description="规则预过滤掉的噪音条数（动画表情等）")
    dup_raw: int = Field(default=0, description="原文重复条数（群内刷屏重复发送，去重后不送模型）")
    batches: int = Field(description="分批数")
    llm_valid: int = Field(description="弱模型判定为有效兼职的条数")
    new_inserted: int = Field(description="新入库条数")
    dup_in_run: int = Field(description="run 内重复（模型重复输出）条数")
    dup_jsonl: int = Field(description="jsonl 层重复条数")
    dup_db: int = Field(description="数据库层重复条数")
    commute_failed: int = Field(default=0, description="通勤未计算条数（高德 MCP 不可用/地址无法定位）")
    elapsed_sec: float = Field(description="耗时（秒）")


#2.文本归一化：去首尾空白、折叠内部连续空白（保证同一条消息 hash 稳定）
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """折叠空白用于稳定 content_hash

    Args:
        text: 原始文本

    Returns:
        归一化后的文本（首尾无空白、内部空白折叠为单个空格）
    """
    return _WS_RE.sub(" ", text).strip()


def compute_content_hash(group_name: str, raw_text: str) -> str:
    """计算消息内容指纹（sha256，前 16 位十六进制足够去重）

    Args:
        group_name: 群名
        raw_text: 消息原文

    Returns:
        hex 摘要前 16 位
    """
    payload = f"{group_name}:{normalize_text(raw_text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
