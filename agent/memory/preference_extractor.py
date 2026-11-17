# -*- coding: utf-8 -*-
"""
长期记忆提取：对话结束后（fire-and-forget）用弱模型提取偏好与重要事实，写入 AsyncPostgresStore。

约束：
- 只存跨会话稳定信息（偏好 / 约束 / 用户自述事实）
- 不存兼职列表、联系方式、工具结果、一次性信息
- 与设置页显式偏好冲突时以显式为准（prompt 中给出显式偏好约束模型输出）
- 去重：有向量索引时按相似度合并；无索引用文本哈希作 key
- namespace ("default","insights")，value 顶层 "text" 字段做向量
"""
import hashlib
import re
from datetime import datetime

from loguru import logger

from agent.config import get_settings
from agent.models.memory import MemoryBatch
from agent.models_factory import get_memory_extractor, is_model_configured
from agent.preferences import load_chat_preferences

NAMESPACE = ("default", "insights")
SIMILARITY_THRESHOLD = 0.86

EXTRACT_PROMPT = """你是用户长期记忆提取器。根据下面这轮对话，提取值得跨会话记住的信息。

只提取三类：
1. preference（偏好）：佣金期望、通勤距离/方式、体力强度、时间偏好、结算方式、地点/行业偏好等。
2. constraint（约束）：明确不接受的条件（如"不接夜班""不要押金""不做重体力"）。
3. fact（用户自述事实）：与找兼职相关的稳定事实（如"我在上海普陀""每周三有空"）。

不要提取：具体兼职列表、联系方式、一次性的日期安排、工具返回的数据、寒暄、密码等敏感信息。
用户明确纠正旧偏好时，以本轮说法为准。

已知显式偏好（设置页，最高优先级，提取结果不得与之矛盾）：
{explicit}

对话：
用户：{user}
助手：{assistant}

没有可提取内容时 items 返回空列表。"""

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _memory_key(item) -> str:
    """确定性 key（无向量索引时的去重依据）"""
    payload = f"{item.kind}:{_normalize(item.text)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def extract_and_store_memory(
    store, thread_id: str, user_message: str, assistant_reply: str
) -> int:
    """提取并写入长期记忆，返回写入条数（任何失败静默降级）"""
    if store is None:
        return 0
    if not is_model_configured("weak"):
        logger.debug("弱模型未配置，跳过长期记忆提取")
        return 0
    text = _normalize(user_message)
    if len(text) < 4:
        return 0

    explicit = load_chat_preferences(get_settings().preferences)

    def _limit(value):
        return f"{value} 分钟" if value else "不限制"

    explicit_text = (
        f"最低佣金 {explicit.commission_min:g} 元；"
        f"骑行通勤上限 {_limit(explicit.max_commute_min)}；"
        f"公共交通上限 {_limit(explicit.max_commute_transit_min)}；"
        f"驾车出行上限 {_limit(explicit.max_commute_drive_min)}；"
        f"体力偏好={explicit.labor_level}；"
        f"每日工作时长={explicit.daily_hours_max or '不限'}；"
        f"工作时段={explicit.work_time_start}-{explicit.work_time_end}；"
        f"通勤起点={explicit.commute_origin or '未配置'}；其他={explicit.note or '无'}"
    )
    prompt = EXTRACT_PROMPT.format(
        explicit=explicit_text,
        user=text[:1500],
        assistant=_normalize(assistant_reply or "")[:1500],
    )

    try:
        batch: MemoryBatch = await get_memory_extractor().ainvoke(prompt)
    except Exception:
        logger.opt(exception=True).warning("长期记忆提取失败")
        return 0

    saved = 0
    has_index = bool(getattr(store, "index_config", None))
    for item in batch.items:
        key = _memory_key(item)
        try:
            #1.有向量索引：相似记忆合并到同一 key
            if has_index:
                hits = await store.asearch(NAMESPACE, query=_normalize(item.text), limit=1)
                if hits and (getattr(hits[0], "score", None) or 0) >= SIMILARITY_THRESHOLD:
                    key = hits[0].key
            await store.aput(
                NAMESPACE,
                key,
                {
                    "text": _normalize(item.text),
                    "kind": item.kind,
                    "confidence": float(item.confidence),
                    "source_thread": thread_id,
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                index=["text"],
            )
            saved += 1
        except Exception:
            logger.opt(exception=True).warning("写入长期记忆失败 key={}", key)

    if saved:
        logger.info("长期记忆已更新 {} 条（thread={}）", saved, thread_id)
    return saved