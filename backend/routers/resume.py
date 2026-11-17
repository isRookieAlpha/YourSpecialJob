# -*- coding: utf-8 -*-
"""简历上传与管理路由：上传解析入库、列表、删除、重建索引。"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from loguru import logger

from agent import rag
from agent.db import resume_dao
from backend.deps import get_pool

router = APIRouter(prefix="/api/resume", tags=["resume"])

MAX_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


@router.post("/upload", status_code=201)
async def upload_resume(
    request: Request,
    file: UploadFile = File(description="简历文件（PDF/DOCX/TXT/MD，≤10MB）"),
):
    """上传简历 → 解析 → 切块 → embedding → 入库（同步处理，通常几秒）"""
    filename = file.filename or "resume"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(422, detail="仅支持 PDF/DOCX/TXT/MD 文件")
    data = await file.read()
    if len(data) > MAX_SIZE_BYTES:
        raise HTTPException(413, detail="文件超过 10MB 限制")
    if not data:
        raise HTTPException(422, detail="文件为空")

    #1.先解析（失败不落盘）
    try:
        text = rag.extract_text(filename, data)
    except rag.ResumeParseError as e:
        raise HTTPException(422, detail=str(e))

    #2.落盘 + 登记，再索引
    stored_path = rag.save_resume_file(filename, data)
    pool = get_pool(request)
    file_id = await resume_dao.create_file(
        pool,
        filename=Path(filename).name,
        stored_path=stored_path,
        mime=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    try:
        chunk_count = await rag.index_resume(pool, file_id, text)
    except Exception as e:
        logger.exception("简历索引失败 file_id={}", file_id)
        await resume_dao.update_file_status(
            pool, file_id, status="error", error=str(e)[:300]
        )
        Path(stored_path).unlink(missing_ok=True)
        await resume_dao.delete_file(pool, file_id)
        raise HTTPException(500, detail=f"简历索引失败：{e}")

    return {"id": file_id, "filename": Path(filename).name, "chunk_count": chunk_count}


@router.get("/files")
async def list_resume_files(request: Request):
    pool = get_pool(request)
    return {"files": await resume_dao.list_files(pool)}


@router.delete("/files/{file_id}")
async def delete_resume_file(request: Request, file_id: int):
    pool = get_pool(request)
    stored_path = await resume_dao.delete_file(pool, file_id)
    if stored_path is None:
        raise HTTPException(404, detail="简历不存在")
    Path(stored_path).unlink(missing_ok=True)
    return {"deleted": True}


@router.post("/rebuild")
async def rebuild_resume_index(request: Request):
    """用当前 embedding 模型重建全部简历索引（换模型/维度后使用）"""
    pool = get_pool(request)
    files = await resume_dao.list_files(pool)
    total_chunks = 0
    failed: list[dict] = []
    for item in files:
        try:
            path = Path(item["stored_path"])
            if not path.exists():
                raise FileNotFoundError("原始文件不存在，请重新上传")
            text = rag.extract_text(item["filename"], path.read_bytes())
            total_chunks += await rag.index_resume(pool, item["id"], text)
        except Exception as e:
            logger.warning("重建简历索引失败 id={}：{}", item["id"], e)
            failed.append({"id": item["id"], "error": str(e)[:200]})
            await resume_dao.update_file_status(
                pool, item["id"], status="error", error=str(e)[:300]
            )
    return {"files": len(files), "chunks": total_chunks, "failed": failed}