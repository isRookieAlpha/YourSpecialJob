# -*- coding: utf-8 -*-
"""心动的offer 路由：CRUD / 状态流转 / 导出 PDF / 静态地图。"""
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, Field

from agent.amap_map import build_static_map
from agent.db.offers_dao import (
    create_manual_offer,
    delete_offer,
    get_offers_by_ids,
    list_offers,
    set_offer_status,
    update_offer,
)
from agent.models.offer import ManualOfferInput, OfferStatusPatch, OfferUpdateInput
from agent.report_pdf import render_jobs_pdf
from backend.deps import get_pool

router = APIRouter(prefix="/api/offers", tags=["offers"])


class OfferExportRequest(BaseModel):
    """导出 ToDo PDF（缺省导出全部 ToDo）"""

    job_ids: list[int] | None = Field(default=None, description="选中的 ToDo id 列表")
    title: str | None = Field(default=None, description="报告标题")


def _stats(all_rows: list[dict]) -> dict:
    """统计：数量与预计总收入（按佣金中值估算）"""
    todo_rows = [r for r in all_rows if r["offer_status"] == "todo"]
    return {
        "favorite_count": sum(1 for r in all_rows if r["offer_status"] == "favorite"),
        "todo_count": len(todo_rows),
        "estimated_total_income": round(sum(r["estimated_income"] for r in todo_rows), 2),
        "income_unknown_count": sum(1 for r in todo_rows if r["income_unknown"]),
    }


@router.get("")
async def get_offers(request: Request, status: str = "favorite,todo", limit: int = 200):
    """心动的offer / ToDo 列表（stats 始终针对全部 favorite+todo 计算）"""
    statuses = tuple(s.strip() for s in status.split(",") if s.strip())
    if not statuses:
        statuses = ("favorite", "todo")
    pool = get_pool(request)
    all_rows = await list_offers(pool, statuses=("favorite", "todo"))
    items = [r for r in all_rows if r["offer_status"] in statuses][: max(1, min(limit, 500))]
    return {"items": items, "stats": _stats(all_rows)}


@router.post("", status_code=201)
async def create_offer(body: ManualOfferInput, request: Request):
    """手动录入兼职（直接入库，不经大模型）"""
    return await create_manual_offer(get_pool(request), body)


@router.patch("/status")
async def patch_offer_status(body: OfferStatusPatch, request: Request):
    """状态流转（升级/降级）；promote 模式不会把 ToDo 降级"""
    updated, skipped = await set_offer_status(
        get_pool(request), body.job_ids, body.status, mode=body.mode
    )
    return {"items": updated, "skipped": skipped}


@router.post("/export")
async def export_offers(body: OfferExportRequest, request: Request):
    """导出 ToDo PDF 到设置目录（默认桌面）"""
    pool = get_pool(request)
    if body.job_ids:
        rows = await get_offers_by_ids(pool, body.job_ids)
    else:
        rows = await list_offers(pool, statuses=("todo",))
    todo_rows = [r for r in rows if r["offer_status"] == "todo"]
    if not todo_rows:
        raise HTTPException(400, detail="没有可导出的 ToDo 待办offer")

    total_income = round(sum(r["estimated_income"] for r in todo_rows), 2)
    try:
        path = render_jobs_pdf(
            todo_rows,
            filters_summary=f"心动的offer · ToDo 待办清单（{len(todo_rows)} 条，预计收入 {total_income:g} 元）",
            title=body.title or "ToDo待办offer报告",
            filename_prefix="offer_todo_report",
        )
    except Exception as e:
        logger.exception("ToDo 报告导出失败")
        raise HTTPException(500, detail=f"报告导出失败：{e}")

    return {
        "path": str(path),
        "filename": path.name,
        "count": len(todo_rows),
        "estimated_total_income": total_income,
    }


@router.get("/map.png")
async def get_offers_map(request: Request):
    """ToDo 地点静态地图（高德 Web 服务 key，后端代理返回 PNG）

    图例（A/B/C → 地点 → 关联兼职）放在 X-Map-Legend 响应头（URL 编码 JSON），
    供前端展示"哪个大头针对应哪些兼职"。
    """
    pool = get_pool(request)
    rows = await list_offers(pool, statuses=("todo",))
    addresses = [r.get("location") for r in rows if r.get("location")]
    try:
        png, skipped, located = await build_static_map(addresses)
    except RuntimeError as e:
        message = str(e)
        if "未配置高德" in message:
            raise HTTPException(503, detail=message)
        if "没有可展示" in message:
            raise HTTPException(404, detail=message)
        raise HTTPException(502, detail=message)

    #1.组装图例：label 顺序与 markers 一致（A-J 对应已定位地址）
    normalize = lambda s: " ".join(str(s or "").split())  # noqa: E731
    titles_by_loc: dict[str, list[str]] = {}
    for row in rows:
        loc = normalize(row.get("location"))
        if loc:
            titles_by_loc.setdefault(loc, []).append(row.get("work_summary") or "未命名")
    labels = "ABCDEFGHIJ"
    legend = [
        {"label": labels[i], "location": addr, "titles": titles_by_loc.get(addr, [])}
        for i, addr in enumerate(located[:10])
    ]

    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Map-Skipped": str(skipped),
            "X-Map-Legend": quote(json.dumps(legend, ensure_ascii=False), safe=""),
        },
    )


@router.put("/{job_id}")
async def put_offer(job_id: int, body: OfferUpdateInput, request: Request):
    """编辑兼职（白名单字段）"""
    updated = await update_offer(get_pool(request), job_id, body)
    if updated is None:
        raise HTTPException(404, detail="兼职不存在")
    return updated


@router.delete("/{job_id}")
async def remove_offer(job_id: int, request: Request):
    """删除（手动录入硬删）/ 移除（其他来源软删）"""
    removed, mode = await delete_offer(get_pool(request), job_id)
    if not removed:
        raise HTTPException(404, detail="兼职不存在")
    return {"removed": True, "mode": mode}