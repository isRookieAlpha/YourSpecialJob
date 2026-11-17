# -*- coding: utf-8 -*-
"""
长期跟踪调度器：定时多群增量读取 → 指纹过滤 → 入库 → 偏好命中推送微信。

约束：
- 每轮从 DB 重读 preferences（设置保存后立即生效，无需重建 agent）
- pg_try_advisory_lock 保证多实例只跑一份；连接断开自动释放
- 首次（initialized_at 为空）为基线：入库后同样推送（用户要求基线也推送，仅标记初始化状态）
- 单群异常隔离；CancelledError 上抛；推送失败不阻塞入库
"""
import asyncio
import os
import time
from datetime import date, datetime, timedelta
from uuid import uuid4

from langchain_core.messages import AIMessage
from loguru import logger

from agent.amap_travel import weather_today
from agent.config import get_settings
from agent.db.jobs_dao import JobQueryFilters, get_jobs_by_ids, query_jobs
from agent.db.pool import get_pool
from agent.db.settings_dao import get_raw_settings
from agent.db.tracking_dao import (
    cleanup_ledger,
    cleanup_push_log,
    ensure_group_state,
    filter_known_content,
    filter_unpushed,
    filter_unseen,
    get_group_state,
    list_retry_job_ids,
    mark_group_initialized,
    message_fingerprint,
    record_push,
    record_seen,
    touch_group_state,
)
from agent.ingest import _prefilter, ingest_messages
from agent.mcp_wechat import (
    call_wechat_get_history,
    find_source_ref,
    send_wechat_message,
    to_mcp_date,
)
from agent.models.job import compute_content_hash
from agent.models_factory import is_model_configured
from agent.preferences import TrackingConfig, load_tracking_config
from agent.tracking_format import build_push_summary
from agent.wechat_window import ingest_run_lock, wechat_window_lock

ADVISORY_LOCK_KEY = 0x59534A4D37  # 'YSJM7'
ROBOT_PREFIX = "【YourSpecialJob"  # 推送摘要前缀（防自我摄入）
READ_TIMEOUT_SEC = 180  # 后台读取超时（比手动 600s 短，避免长时间占窗）
PUSH_RETRY_MAX = 3
PUSH_RETRY_DELAY_SEC = 3  # 轮内发送失败的重试间隔（测试可置 0）


class TrackingState:
    """跟踪器内存状态（状态接口/health 用）"""

    def __init__(self):
        self.running = False
        self.last_run_at: str | None = None
        self.last_ok: bool | None = None
        self.last_error: str | None = None
        self.last_stats: dict = {}
        #1.本轮进度（每个群一个单位；label 为当前处理的群/阶段）
        self.progress: dict = {"current": 0, "total": 0, "label": ""}
        self.worker_pid = os.getpid()

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "last_run_at": self.last_run_at,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_stats": self.last_stats,
            "progress": dict(self.progress),
            "worker_pid": self.worker_pid,
        }


async def load_config(pool) -> TrackingConfig:
    """每轮从 DB 读取 preferences（失败回退 .env 侧 settings）"""
    try:
        data = await get_raw_settings(pool)
        return load_tracking_config(data.get("preferences") or {})
    except Exception:
        logger.warning("读取 app_settings 失败，回退 .env 偏好", exc_info=True)
        return load_tracking_config(get_settings().preferences or {})


def _is_robot_message(message: dict) -> bool:
    """机器人自己推送的摘要（防止推送目标恰好在监控群时被自我摄入）"""
    return str(message.get("消息", "")).strip().startswith(ROBOT_PREFIX)


async def process_group(
    pool, cfg: TrackingConfig, group: str, round_start: datetime, state: TrackingState
) -> dict:
    """单群增量处理（读取→指纹→入库），返回本轮统计"""
    day = date.today()
    label = to_mcp_date(day)
    await ensure_group_state(pool, group)
    group_state = await get_group_state(pool, group) or {}
    baseline = group_state.get("initialized_at") is None

    try:
        #1.读取当天记录（窗口锁只覆盖读取）
        async with ingest_run_lock:
            async with wechat_window_lock:
                messages = await call_wechat_get_history(
                    group, label, allow_empty=True, timeout=READ_TIMEOUT_SEC
                )

            candidates, noise, dup = _prefilter(messages)
            candidates = [m for m in candidates if not _is_robot_message(m)]

            #2.指纹过滤（未见才继续）
            fp_by_message: dict[str, dict] = {}
            fps: list[str] = []
            for m in candidates:
                fp = message_fingerprint(
                    group, day, m.get("发送者", ""), m.get("时间", ""), m.get("消息", "")
                )
                fp_by_message[fp] = m
                fps.append(fp)
            unseen = await filter_unseen(pool, group, day, fps)

            #3.内容哈希过滤（jobs 已存在 → 只记指纹不送模型）
            hash_by_fp = {
                fp: compute_content_hash(group, str(fp_by_message[fp].get("消息", "")))
                for fp in unseen
            }
            known = await filter_known_content(pool, group, list(hash_by_fp.values()))
            skip_fps = [fp for fp in unseen if hash_by_fp[fp] in known]
            extract_fps = [fp for fp in unseen if hash_by_fp[fp] not in known]

            #4.单轮送模型上限（其余留到下一轮）
            overflow = len(extract_fps) - cfg.max_messages
            if overflow > 0:
                logger.warning("群 {} 单轮待提取 {} 条超过上限，本轮只处理 {}", group, len(extract_fps), cfg.max_messages)
                extract_fps = extract_fps[: cfg.max_messages]

            to_extract = [fp_by_message[fp] for fp in extract_fps]
            stats: dict = {
                "group": group, "total": len(messages), "noise": noise, "dup_raw": dup,
                "new_messages": len(unseen), "known_content": len(skip_fps),
                "sent_to_model": len(to_extract), "baseline": baseline,
                "new_inserted": 0,
            }
            if to_extract:
                ingest_stats = await ingest_messages(
                    group,
                    label,
                    day,
                    to_extract,
                    source="wechat_auto",
                    source_ref=find_source_ref(group),
                )
                stats["new_inserted"] = ingest_stats.new_inserted

            #5.记录已见指纹（送模型的成功后再记；内容已知的直接记）
            await record_seen(pool, group, day, [
                {
                    "fingerprint": fp,
                    "sender": fp_by_message[fp].get("发送者"),
                    "msg_time": fp_by_message[fp].get("时间"),
                }
                for fp in (extract_fps + skip_fps)
            ])

        #6.基线：仅标记初始化完成（基线入库的数据同样参与推送，不标 skipped）
        if baseline:
            await mark_group_initialized(pool, group)

        await touch_group_state(pool, group, error=None)
        return stats
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("跟踪群 {} 处理失败", group)
        await touch_group_state(pool, group, error=f"{type(e).__name__}: {e}"[:500])
        return {"group": group, "error": f"{type(e).__name__}: {e}"}


async def _remember_push(agent, conversation: str, summary: str) -> None:
    """把推送摘要写入互动线程的短期记忆（agent 后续能引用"这条推送"；失败不影响推送）"""
    if agent is None or not conversation:
        return
    try:
        await agent.aupdate_state(
            {"configurable": {"thread_id": f"wechat:{conversation}"}},
            {"messages": [AIMessage(content=summary)]},
        )
        logger.info("推送摘要已写入短期记忆 thread=wechat:{}", conversation)
    except Exception:
        logger.warning("推送摘要写入短期记忆失败（不影响推送）", exc_info=True)


async def push_round(
    pool, cfg: TrackingConfig, state: TrackingState, dry_run: bool = False,
    round_start: datetime | None = None, agent=None,
) -> dict:
    """偏好命中推送（所有群处理完后统一发一条；dry_run 只生成摘要不发送）

    round_start 为本轮开始时间：候选只取"本轮新入库"（前后留 60s 余量），
    不会把 lookback 窗口内的历史未推送记录一次性捞出。
    推送目标：preferences.wechat_target 优先，空则回退 .env 的 WECHAT_SENDER_ID。
    """
    prefs = getattr(get_settings(), "preferences", None) or {}
    sender = str(prefs.get("wechat_target") or "").strip() or get_settings().wechat_sender_id.strip()
    if not cfg.push_enabled:
        return {"pushed": 0, "reason": "推送未启用"}
    if not sender:
        return {"pushed": 0, "reason": "WECHAT_SENDER_ID 未配置"}
    if sender in cfg.groups:
        logger.warning("推送目标与监控群同名，已跳过推送（防止自我摄入）：{}", sender)
        return {"pushed": 0, "reason": "推送目标在监控群中"}

    day = date.today()
    # 本轮新入库的时间下界：以 round_start 为准（留 60s 余量）；无 round_start（probe）时回退 lookback
    if round_start is not None:
        ingested_after = round_start - timedelta(seconds=60)
    else:
        ingested_after = datetime.now().astimezone() - timedelta(minutes=cfg.push_lookback_min)

    #1.各群本轮新入库（偏好过滤；labor_level 优先，兼容旧 prefer_light）
    physical = {"light": ["轻"], "heavy": ["重"]}.get(cfg.labor_level)
    if physical is None and cfg.prefer_light:
        physical = ["轻"]
    rows: list[dict] = []
    for group in cfg.groups:
        rows.extend(await query_jobs(pool, JobQueryFilters(
            group_name=group,
            source="wechat_auto",
            date_start=day,
            commission_min=cfg.commission_min,
            physical_intensities=physical,
            ingested_after=ingested_after,
            limit=50,
        )))

    #2.失败重试候选（不受 lookback 限制）
    retry_ids = await list_retry_job_ids(pool, PUSH_RETRY_MAX)
    if retry_ids:
        rows.extend(await get_jobs_by_ids(pool, retry_ids))

    #3.去重与二次过滤
    seen_ids: set[int] = set()
    filtered: list[dict] = []
    for row in rows:
        jid = row["id"]
        if jid in seen_ids:
            continue
        seen_ids.add(jid)
        if row.get("offer_status") != "none":
            continue
        if row.get("work_date") and row["work_date"] < day.isoformat():
            continue
        commission_max = row.get("commission_max")
        if commission_max is None or commission_max < (cfg.commission_min or 0):
            continue
        filtered.append(row)

    unpushed = await filter_unpushed(pool, [r["id"] for r in filtered])
    filtered = [r for r in filtered if r["id"] in unpushed]
    if not filtered:
        return {"pushed": 0, "reason": "无新增命中"}

    #4.排序与截断
    filtered.sort(key=lambda r: (r.get("commission_max") or 0), reverse=True)
    top = filtered[: cfg.push_limit]

    #5.天气（通勤已在入库时计算并落库，直接复用 jobs.commute_* 字段）
    weather_text = ""
    try:
        weather = await weather_today(cfg.amap_city or None)
        if weather:
            weather_text = f"天气：{weather.city} {weather.weather} {weather.temperature}℃ {weather.windpower}级风"
    except Exception:
        logger.warning("推送天气获取失败", exc_info=True)

    commute_limits = (
        ("commute_bike_min", cfg.max_commute_min),
        ("commute_transit_min", cfg.max_commute_transit_min),
        ("commute_drive_min", cfg.max_commute_drive_min),
    )
    if cfg.commute_origin and any(limit for _, limit in commute_limits):
        for row in top:
            row["commute_over_limit"] = any(
                row.get(field) is not None and limit is not None and row[field] > limit
                for field, limit in commute_limits
            )
        top = [r for r in top if not r.get("commute_over_limit")]
        if not top:
            return {"pushed": 0, "reason": "候选均超通勤上限"}

    summary = build_push_summary(top, total_candidates=len(filtered), weather_text=weather_text)
    if dry_run:
        return {"pushed": 0, "dry_run": True, "summary": summary, "candidates": len(top)}

    batch_id = uuid4().hex[:8]
    overflow_ids = [r["id"] for r in filtered[cfg.push_limit:]]

    #6.发送（失败重试一次）
    try:
        async with wechat_window_lock:
            await send_wechat_message(sender, summary)
    except Exception:
        await asyncio.sleep(PUSH_RETRY_DELAY_SEC)
        try:
            async with wechat_window_lock:
                await send_wechat_message(sender, summary)
        except Exception as e:
            logger.error("推送失败（已重试）：{}", e)
            await record_push(
                pool, [r["id"] for r in top], "failed", batch_id=batch_id,
                preview=summary[:200], error=str(e)[:300], increment_attempt=True,
            )
            return {"pushed": 0, "reason": f"发送失败：{type(e).__name__}"}

    await record_push(
        pool, [r["id"] for r in top], "sent", batch_id=batch_id, preview=summary[:200]
    )
    if overflow_ids:
        await record_push(pool, overflow_ids, "skipped", batch_id=batch_id, error="overflow")
    #7.推送摘要写入互动线程短期记忆（用户回复"这条"时 agent 能引用）
    await _remember_push(agent, sender, summary)
    logger.info("推送成功：{} 条（批次 {}）", len(top), batch_id)
    return {"pushed": len(top), "batch_id": batch_id, "summary_preview": summary[:120]}


async def run_round(pool, state: TrackingState, cfg: TrackingConfig,
                    force: bool = False, agent=None) -> dict:
    """执行一轮（advisory lock 保护）"""
    #1.微信访问总开关优先于手动触发（设置页关闭时禁止操作微信）
    if not cfg.wechat_access_enabled:
        state.last_ok = False
        state.last_error = "微信访问已关闭"
        return {"skipped": True, "reason": "微信访问已关闭"}
    if not cfg.enabled and not force:
        return {"skipped": True, "reason": cfg.paused_reason}
    if not is_model_configured("weak"):
        state.last_ok = False
        state.last_error = "弱模型未配置"
        return {"skipped": True, "reason": "弱模型未配置"}

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        locked = (await cur.fetchone())[0]
        if not locked:
            logger.info("跟踪轮次跳过：其他实例持有锁")
            return {"skipped": True, "reason": "其他实例运行中"}

        try:
            round_start = datetime.now().astimezone()
            state.running = True
            state.last_run_at = round_start.isoformat(timespec="seconds")
            state.progress = {"current": 0, "total": len(cfg.groups), "label": "准备读取"}
            group_stats = []
            for group in cfg.groups:
                state.progress["label"] = group
                group_stats.append(await process_group(pool, cfg, group, round_start, state))
                state.progress["current"] += 1
            state.progress["label"] = "推送摘要"
            push_result = await push_round(pool, cfg, state, round_start=round_start, agent=agent)

            #7.每日清理一次（指纹保留 N 天、推送日志 30 天）
            await cleanup_ledger(pool, cfg.ledger_days)
            await cleanup_push_log(pool, 30)

            state.last_ok = True
            state.last_error = None
            state.last_stats = {
                "groups": group_stats,
                "new_inserted": sum(s.get("new_inserted", 0) for s in group_stats),
                "pushed": push_result.get("pushed", 0),
            }
            logger.info("跟踪轮次完成：{}", state.last_stats)
            return state.last_stats
        except asyncio.CancelledError:
            raise
        except Exception as e:
            state.last_ok = False
            state.last_error = f"{type(e).__name__}: {e}"[:500]
            logger.exception("跟踪轮次异常")
            return {"error": state.last_error}
        finally:
            state.running = False
            state.progress["label"] = ""
            await conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


async def tracking_loop(app) -> None:
    """固定节拍调度循环（异常不退出循环）"""
    pool = await get_pool()
    state: TrackingState = app.state.tracking_state
    wakeup: asyncio.Event = app.state.tracking_wakeup
    next_run = time.time()
    force_once = False

    logger.info("跟踪调度器已启动（pid={}）", state.worker_pid)
    while True:
        try:
            if force_once:
                wakeup.clear()
                force, force_once = True, False
            elif wakeup.is_set():
                wakeup.clear()
                force = True
            else:
                force = False

            cfg = await load_config(pool)
            if cfg.enabled or force:
                # 注意参数顺序：run_round(pool, state, cfg)；agent 用于推送摘要写入短期记忆
                await run_round(
                    pool, state, cfg, force=force,
                    agent=getattr(app.state, "agent", None),
                )
                next_run = time.time() + cfg.interval_min * 60
            else:
                next_run = time.time() + 60

            timeout = max(10.0, next_run - time.time())
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=timeout)
                force_once = True  # 被唤醒 → 立即再跑一轮
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            logger.info("跟踪调度器已取消")
            raise
        except Exception as e:
            # 异常必须写回状态，前端/health 才能看到原因
            state.running = False
            state.last_ok = False
            state.last_error = f"{type(e).__name__}: {e}"[:500]
            state.last_run_at = datetime.now().astimezone().isoformat(timespec="seconds")
            logger.exception("跟踪调度器异常，60 秒后重试")
            force_once = True  # 下一轮强制重试一次
            # 60 秒等待可被手动触发打断
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass


def start_tracking(app) -> asyncio.Task:
    """lifespan 启动跟踪任务"""
    app.state.tracking_state = TrackingState()
    app.state.tracking_wakeup = asyncio.Event()
    task = asyncio.create_task(tracking_loop(app))
    app.state.tracking_task = task
    return task


async def stop_tracking(app) -> None:
    """lifespan 退出取消跟踪任务（最多等 10 秒）"""
    task = getattr(app.state, "tracking_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        logger.warning("跟踪任务未在 10 秒内退出，进程退出将强制终止")