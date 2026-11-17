# -*- coding: utf-8 -*-
"""系统辅助路由：高德区划代理 + 系统目录选择（后端 tkinter）。"""
import asyncio
import threading

from fastapi import APIRouter, HTTPException, Query

from agent.amap_district import fetch_districts

router = APIRouter(prefix="/api/system", tags=["system"])

_picker_lock = threading.Lock()


def _pick_directory_blocking() -> str | None:
    """在独立线程内创建 Tk 目录选择框（取消返回 None）"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:  # 无 tkinter 的环境
        raise RuntimeError("当前环境不支持系统目录选择，请手动输入路径") from e

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title="选择 PDF 导出目录") or None
    finally:
        root.destroy()


@router.get("/districts")
async def get_districts(adcode: str = Query("100000", description="上级行政区 adcode")):
    """省/市/区三级联动数据（只返回名称与 adcode）"""
    try:
        return {"districts": await fetch_districts(adcode)}
    except RuntimeError as e:
        detail = str(e)
        raise HTTPException(503 if "未配置" in detail else 502, detail=detail)


@router.post("/pick-directory")
async def pick_directory():
    """打开 Windows 系统目录选择框（取消返回 path=null）"""
    if not _picker_lock.acquire(blocking=False):
        raise HTTPException(409, detail="目录选择框已打开，请先完成选择")
    try:
        try:
            path = await asyncio.to_thread(_pick_directory_blocking)
        except RuntimeError as e:
            raise HTTPException(501, detail=str(e))
    finally:
        _picker_lock.release()
    return {"path": path, "cancelled": path is None}