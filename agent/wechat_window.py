# -*- coding: utf-8 -*-
"""
微信窗口互斥锁（pywechat 的读取/发送都是 UI 自动化，必须串行）。

两级锁，获取顺序固定为：先 ingest_run_lock，再 wechat_window_lock：
- ingest_run_lock：保护整条摄入管道（防止同一时刻重复跑弱模型提取）
- wechat_window_lock：只保护实际操作的微信窗口（读取/发送），
  弱模型提取期间不占用窗口锁，避免发送等太久
发送路径只取 wechat_window_lock，因此不会与摄入管道死锁。
"""
import asyncio
from contextlib import asynccontextmanager

#1.两级锁（模块级单例，进程内共享）
ingest_run_lock = asyncio.Lock()
wechat_window_lock = asyncio.Lock()


@asynccontextmanager
async def exclusive_wechat_window():
    """独占微信窗口（读取/发送期间调用方必须持有）"""
    async with wechat_window_lock:
        yield