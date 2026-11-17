# -*- coding: utf-8 -*-
"""
全库通勤重算 worker：设置页修改通勤起始位置后触发。

策略：
- 任务开始时快照 origin/city（避免中途保存导致每批读不同配置）
- 起点先用 REST geocode 预检，失败则不清空任何数据
- 每批先计算，成功后才清空该批旧值并写回；失败批保留旧数据（避免起点错误导致全库丢值）
- 按唯一地点每 60 个一批（每地点约 4 次 MCP 调用 × 0.45s，避免 600s 总超时）
- 单批失败计入 failed 并继续；处理中再次触发 → pending，本轮结束后再跑一轮
"""
import asyncio
import math
import os
from datetime import datetime

import httpx
from loguru import logger

from agent.amap_map import geocode_info
from agent.amap_mcp import AmapMcpError, compute_commute_batch
from agent.commute_service import (
    clear_commute_fields,
    list_commute_targets,
    persist_commute,
)
from agent.config import get_settings
from agent.db.pool import get_pool

COMMUTE_REFRESH_LOCK_KEY = 0x59534A4D39
UNIQUE_LOCATIONS_PER_BATCH = 60
MAX_FAILURE_ITEMS = 50  # 失败明细最多保留条数


class CommuteRefreshState:
    """重算任务状态（内存；进程重启后重置）"""

    def __init__(self):
        self.running = False
        self.pending = False
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.total = 0
        self.processed = 0
        self.updated = 0
        self.failed = 0
        self.skipped = 0
        self.batch = 0
        self.batches = 0
        self.origin = ""
        self.city = ""
        self.last_error: str | None = None
        #1.失败明细（最多 50 条）：{location, reason, count}
        self.failures: list[dict] = []
        self.worker_pid = os.getpid()

    def reset(self) -> None:
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.finished_at = None
        self.total = 0
        self.processed = 0
        self.updated = 0
        self.failed = 0
        self.skipped = 0
        self.batch = 0
        self.batches = 0
        self.last_error = None
        self.failures = []

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "pending": self.pending,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "processed": self.processed,
            "updated": self.updated,
            "failed": self.failed,
            "skipped": self.skipped,
            "batch": self.batch,
            "batches": self.batches,
            "origin": self.origin,
            "city": self.city,
            "last_error": self.last_error,
            "failures": list(self.failures),
            "worker_pid": self.worker_pid,
        }


def _add_failure(state: CommuteRefreshState, location: str,
                 reason: str, count: int = 1) -> None:
    """记录一条失败明细（超过上限则忽略）"""
    if len(state.failures) >= MAX_FAILURE_ITEMS:
        return
    state.failures.append({"location": location, "reason": reason, "count": count})


async def _check_origin(origin: str, city: str) -> bool:
    """起点 geocode 预检（失败不清空旧数据）"""
    key = get_settings().amap_server_key
    if not key:
        return False
    async with httpx.AsyncClient(timeout=5) as client:
        info = await geocode_info(client, origin, key, city)
    return bool(info)


async def refresh_once(
    pool, state: CommuteRefreshState, origin: str, city: str
) -> dict:
    """执行一轮全库重算（调用方需持有 advisory lock）"""
    state.origin, state.city = origin, city

    #1.读取全库目标并预检起点
    targets = await list_commute_targets(pool)
    state.total = len(targets)
    if not targets:
        state.last_error = "没有可重算的兼职（location 为空）"
        return state.snapshot()
    try:
        if not await _check_origin(origin, city):
            state.last_error = f"通勤起点无法定位：{origin}（未清空旧数据）"
            _add_failure(state, origin, "通勤起点无法定位（未清空旧数据）", 1)
            return state.snapshot()
    except Exception as e:
        state.last_error = f"起点预检失败：{type(e).__name__}: {e}"[:300]
        _add_failure(state, origin, f"起点预检失败：{type(e).__name__}", 1)
        return state.snapshot()

    #2.按唯一地点分组并分批
    by_location: dict[str, list[dict]] = {}
    for row in targets:
        by_location.setdefault(str(row["location"]).strip(), []).append(row)
    unique_locations = list(by_location)
    state.batches = math.ceil(len(unique_locations) / UNIQUE_LOCATIONS_PER_BATCH)
    rows_per_location = {loc: len(rows) for loc, rows in by_location.items()}

    #3.逐批计算：成功后才清空该批旧值并写回（失败批保留旧数据，避免起点错误导致全库丢值）
    #   进度按地点实时回调（每地点约 2 秒），前端轮询可看到平滑推进
    def _bump_progress(address: str, _ok: bool) -> None:
        state.processed += rows_per_location.get(address, 0)

    for batch_index in range(state.batches):
        start = batch_index * UNIQUE_LOCATIONS_PER_BATCH
        locations = unique_locations[start:start + UNIQUE_LOCATIONS_PER_BATCH]
        batch_rows = [row for loc in locations for row in by_location[loc]]
        processed_before = state.processed
        batch_failures: list[dict] = []
        try:
            computed = await compute_commute_batch(
                batch_rows, origin, city, on_done=_bump_progress, failures=batch_failures,
            )
            #4.记录本批失败明细（同一地点可能对应多条兼职）
            for item in batch_failures:
                location = str(item.get("location") or "")
                count = sum(
                    1 for row in batch_rows if str(row["location"]).strip() == location
                )
                _add_failure(state, location, item.get("reason") or "未知原因", count or 1)
            #5.只写回有有效数据的结果（全空的地址保持 NULL 并计入 failed）
            valid = {
                job_id: data for job_id, data in computed.items()
                if data and any(value is not None for value in data.values())
            }
            await clear_commute_fields(pool, [row["id"] for row in batch_rows])
            persisted = await persist_commute(pool, valid)
            state.updated += persisted
            state.failed += len(batch_rows) - persisted
        except AmapMcpError as e:
            state.failed += len(batch_rows)
            state.last_error = f"第 {batch_index + 1} 批 MCP 失败：{e}"[:300]
            _add_failure(
                state, f"第 {batch_index + 1} 批（{len(batch_rows)} 个地点）",
                str(e)[:150], len(batch_rows),
            )
            logger.warning("通勤重算批次失败 batch={}：{}", batch_index + 1, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            state.failed += len(batch_rows)
            state.last_error = f"第 {batch_index + 1} 批失败：{type(e).__name__}: {e}"[:300]
            _add_failure(
                state, f"第 {batch_index + 1} 批（{len(batch_rows)} 个地点）",
                f"{type(e).__name__}: {str(e)[:120]}", len(batch_rows),
            )
            logger.exception("通勤重算批次异常 batch={}", batch_index + 1)
        finally:
            #5.批次结束按行数对齐（地址在 geocode 阶段被跳过的行也不会漏计）
            state.processed = processed_before + len(batch_rows)
            state.batch = batch_index + 1
        logger.info(
            "通勤重算进度 {}/{} 批（更新 {}，失败 {}）",
            state.batch, state.batches, state.updated, state.failed,
        )
    return state.snapshot()


async def commute_refresh_loop(app) -> None:
    """等待触发 → 重算一轮（异常不退出；pending 时自动再跑）"""
    pool = await get_pool()
    state: CommuteRefreshState = app.state.commute_refresh_state
    wakeup: asyncio.Event = app.state.commute_refresh_wakeup
    logger.info("通勤重算 worker 已启动（pid={}）", state.worker_pid)

    while True:
        try:
            await wakeup.wait()
            wakeup.clear()
            state.pending = False
            state.running = True
            state.reset()
            prefs = get_settings().preferences or {}
            origin = str(prefs.get("commute_origin") or "").strip()
            city = str(prefs.get("amap_city") or "").strip()
            if not origin:
                state.last_error = "未配置通勤起始位置"
            else:
                async with pool.connection() as conn:
                    cur = await conn.execute(
                        "SELECT pg_try_advisory_lock(%s)", (COMMUTE_REFRESH_LOCK_KEY,)
                    )
                    locked = (await cur.fetchone())[0]
                    if not locked:
                        state.last_error = "其他实例正在重算通勤"
                    else:
                        try:
                            await refresh_once(pool, state, origin, city)
                        finally:
                            await conn.execute(
                                "SELECT pg_advisory_unlock(%s)", (COMMUTE_REFRESH_LOCK_KEY,)
                            )
        except asyncio.CancelledError:
            logger.info("通勤重算 worker 已取消")
            raise
        except Exception as e:
            state.last_error = f"{type(e).__name__}: {e}"[:300]
            logger.exception("通勤重算异常")
        finally:
            state.running = False
            state.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
            #5.处理中再次触发 → 立即再跑一轮（使用最新配置）
            if state.pending:
                state.pending = False
                wakeup.set()


def start_commute_refresh(app) -> asyncio.Task:
    """lifespan 启动通勤重算任务"""
    app.state.commute_refresh_state = CommuteRefreshState()
    app.state.commute_refresh_wakeup = asyncio.Event()
    task = asyncio.create_task(commute_refresh_loop(app))
    app.state.commute_refresh_task = task
    return task


async def stop_commute_refresh(app) -> None:
    """lifespan 退出取消任务（最多等 10 秒）"""
    task = getattr(app.state, "commute_refresh_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        logger.warning("通勤重算任务未在 10 秒内退出")