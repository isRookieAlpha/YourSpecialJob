# -*- coding: utf-8 -*-
"""截图上传路由：POST /api/upload/screenshot（弱模型多模态识别 → 入库）。"""
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger

from agent.config import ROOT_DIR
from agent.models.job import IngestStats
from agent.screenshot import ingest_screenshot, prepare_image

router = APIRouter(prefix="/api/upload", tags=["upload"])

#1.上传限制：单张 ≤8MB；仅 PNG/JPEG/WebP（content-type + pillow 双重校验）
MAX_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}

UPLOAD_DIR = ROOT_DIR / "backend" / "uploads"


def cleanup_uploads(max_age_days: int = 7) -> None:
    """清理超过 N 天的上传文件（lifespan 启动时 best-effort 调用）"""
    if not UPLOAD_DIR.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for p in UPLOAD_DIR.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            logger.warning("清理上传文件失败：{}", p.name)
    if removed:
        logger.info("已清理 {} 个过期上传文件", removed)


@router.post("/screenshot", response_model=IngestStats)
async def upload_screenshot(
    group_name: str = Form(description="群名（去重与血缘用）"),
    target_date: date = Form(description="消息日期（YYYY-MM-DD）"),
    file: UploadFile = File(description="聊天记录截图（PNG/JPEG/WebP，≤8MB）"),
):
    """截图 → 多模态转写 → 结构化入库（识别失败返回中文错误）"""
    #2.content-type 白名单
    if (file.content_type or "") not in ALLOWED_TYPES:
        raise HTTPException(400, detail=f"不支持的图片类型：{file.content_type}，仅支持 PNG/JPEG/WebP")

    data = await file.read()
    #3.大小限制
    if len(data) > MAX_SIZE_BYTES:
        raise HTTPException(413, detail=f"图片过大（{len(data) / 1024 / 1024:.1f}MB），上限 8MB")
    if not data:
        raise HTTPException(400, detail="图片内容为空")

    #4.pillow 打开校验（伪装成图片的文件在这里被拒绝）
    try:
        prepare_image(data)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    #5.落盘（uuid4 重命名防路径穿越）
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.img"
    (UPLOAD_DIR / filename).write_bytes(data)

    try:
        return await ingest_screenshot(
            group_name=group_name,
            target_date=target_date,
            image_bytes=data,
            source_ref=filename,
        )
    except RuntimeError as e:
        logger.warning("截图摄入失败：{}", e)
        raise HTTPException(502, detail=str(e))
    except Exception as e:
        logger.exception("截图摄入异常")
        raise HTTPException(500, detail=f"截图处理失败：{e}")