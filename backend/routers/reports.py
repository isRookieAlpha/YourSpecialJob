# -*- coding: utf-8 -*-
"""报告路由：GET /api/reports（列表）+ GET /api/reports/{filename}（下载，防路径穿越）。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from agent.report_pdf import REPORT_DIR

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
async def list_reports():
    """已生成的 PDF 报告列表（按修改时间倒序）"""
    if not REPORT_DIR.exists():
        return []
    items = sorted(
        (p for p in REPORT_DIR.glob("*.pdf") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "filename": p.name,
            "size": p.stat().st_size,
            "created_at": p.stat().st_mtime,
        }
        for p in items
    ]


@router.get("/{filename}")
async def download_report(filename: str):
    """下载 PDF 报告（basename + .pdf 后缀 + 目录内存在 三重校验防穿越）"""
    safe = Path(filename).name
    if safe != filename or not safe.endswith(".pdf"):
        raise HTTPException(404, detail="报告不存在")
    path = REPORT_DIR / safe
    if not path.is_file():
        raise HTTPException(404, detail="报告不存在")
    return FileResponse(path, media_type="application/pdf", filename=safe)