# -*- coding: utf-8 -*-
"""
截图识别通道：弱模型多模态转写聊天记录截图 → 文本 → 公共管道入库。

用户已确认：不提供 OCR 兜底，弱模型必须支持图片输入；
模型不支持时返回明确中文提示（引导更换多模态模型或改用粘贴导入）。
"""
import base64
import io
from datetime import date

from loguru import logger

from agent.ingest import ingest_messages
from agent.manual_ingest import parse_pasted_text
from agent.models.job import IngestStats
from agent.models_factory import get_llm_weak, is_model_configured

#1.图片预处理：长边超过该值时降采样（控 token 与请求体大小）
MAX_EDGE_PX = 2000
JPEG_QUALITY = 85

#2.转写提示词（逐字照抄，保持行头格式供 parse_pasted_text 解析）
TRANSCRIBE_PROMPT = """这是一张微信群聊天记录截图。请把截图中的所有聊天消息逐条转写为文本，格式严格如下：

发送者 时间
消息内容

要求：
1. 每条消息之间空一行；发送者照抄截图上的昵称；时间照抄截图上的时刻（如 10:15），截图没有时间则只写发送者。
2. 消息内容逐字照抄，不要改写、总结、翻译或补充任何内容。
3. 忽略界面元素（状态栏、返回按钮、输入框、表情图标等），只转写聊天消息。
4. 如果截图中没有可辨认的聊天消息，只输出：无聊天内容。
"""

#3.模型不支持图片时的统一错误文案
MULTIMODAL_UNSUPPORTED_MSG = (
    "当前弱模型不支持图片识别，请在设置页更换支持图片输入的多模态模型"
    "（如 qwen3.5-omni-plus），或改用粘贴文本导入。"
)


def prepare_image(image_bytes: bytes) -> tuple[bytes, str]:
    """图片预处理：校验可打开、长边降采样、统一转 JPEG

    Args:
        image_bytes: 原始图片字节

    Returns:
        (JPEG 字节, mime)

    Raises:
        ValueError: 文件不是有效图片
    """
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        raise ValueError(f"无法识别的图片文件：{e}") from e

    #4.统一 RGB（PNG 透明通道/灰度图转 JPEG 需要）
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_EDGE_PX:
        scale = MAX_EDGE_PX / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), "image/jpeg"


async def transcribe_image(image_bytes: bytes) -> str:
    """弱模型多模态转写截图中的聊天记录（唯一识别路径）

    Args:
        image_bytes: 原始图片字节

    Returns:
        转写文本（发送者/时间/消息 行头格式）

    Raises:
        RuntimeError: 模型不支持图片或调用失败（中文提示）
    """
    if not is_model_configured("weak"):
        raise RuntimeError("弱模型未配置，请先在设置页或 .env 中填写弱模型 API key")

    jpeg_bytes, mime = prepare_image(image_bytes)
    data_url = f"data:{mime};base64,{base64.b64encode(jpeg_bytes).decode('ascii')}"

    try:
        from langchain_core.messages import HumanMessage

        llm = get_llm_weak()
        #1.OpenAI 兼容多模态格式：content blocks 必须包在 HumanMessage 里
        result = await llm.ainvoke([
            HumanMessage(content=[
                {"type": "text", "text": TRANSCRIBE_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ])
        ])
    except Exception as e:
        logger.warning("弱模型多模态转写失败：{}", e)
        raise RuntimeError(f"{MULTIMODAL_UNSUPPORTED_MSG}（错误详情：{type(e).__name__}: {e}）") from e

    content = result.content
    text = content if isinstance(content, str) else str(content)
    if not text.strip() or text.strip() == "无聊天内容":
        raise RuntimeError("截图中未识别到聊天消息，请换一张更清晰的截图或改用粘贴导入。")
    return text


async def ingest_screenshot(
    group_name: str,
    target_date: date,
    image_bytes: bytes,
    source_ref: str,
    progress_cb=None,
) -> IngestStats:
    """截图 → 多模态转写 → 解析 → 公共管道入库（source=manual_image）

    Args:
        group_name: 群名（去重与血缘用）
        target_date: 消息日期（缺省工作日期）
        image_bytes: 原始图片字节
        source_ref: 血缘引用（上传文件名）
        progress_cb: 进度回调（可选）

    Returns:
        摄入统计 IngestStats

    Raises:
        RuntimeError: 转写失败 / 转写结果无法解析
    """
    def report(message: str) -> None:
        logger.info(message)
        if progress_cb is not None:
            try:
                progress_cb(message)
            except Exception:
                pass

    report(f"正在用多模态模型识别截图（{source_ref}）…")
    text = await transcribe_image(image_bytes)
    logger.debug("截图转写结果（前 300 字）：{}", text[:300])

    try:
        messages = parse_pasted_text(text)
    except ValueError as e:
        raise RuntimeError(f"截图转写结果无法解析为聊天消息：{e}") from e

    report(f"转写完成：{len(messages)} 条消息，正在结构化提取…")
    return await ingest_messages(
        group_name=group_name,
        target_date_label=target_date.isoformat(),
        target_d=target_date,
        messages=messages,
        source="manual_image",
        source_ref=source_ref,
        progress_cb=progress_cb,
    )