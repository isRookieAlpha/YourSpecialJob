# -*- coding: utf-8 -*-
"""
微信互动 worker：轮询目标会话 → 唤醒 agent → 回复微信；危险操作经微信确认后执行。

约束（实测与探索结论）：
- 微信 MCP 无监听/未读接口 → 短间隔轮询（默认 60s，可配 30~300s；每次读取需操作窗口 ~10-15s）
- 收发消息的 `发送者` 字段都可能是同一昵称（实测收发双方昵称一致）→ 方向识别只靠
  机器人前缀 `【YourSpecialJob` + 入站指纹账本，不依赖发送者
- `agent.ainvoke` 期间禁止持有窗口锁（工具内部会再次申请 → 死锁）；
  读取/发送各自短持锁，发送后冷却 ≥8 秒再读
- HITL：ainvoke 返回 `__interrupt__` 时保存 pending 并发微信确认，下一轮按「同意/拒绝」resume
"""
import asyncio
import json
import os
import re
import time
from datetime import date, datetime

from langgraph.types import Command
from loguru import logger

from agent.config import get_settings
from agent.db.interaction_dao import (
    claim_pending,
    cleanup_expired,
    clear_pending,
    ensure_state,
    get_contact_pending,
    get_pending,
    get_state,
    mark_initialized,
    save_pending,
    touch_state,
)
from agent.db.pool import get_pool
from agent.db.settings_dao import get_raw_settings
from agent.db.tracking_dao import filter_unseen, message_fingerprint, record_seen
from agent.memory.preference_extractor import extract_and_store_memory
from agent.mcp_wechat import (
    WechatMcpError,
    call_wechat_get_history,
    send_wechat_message,
    to_mcp_date,
)
from agent.preferences import InteractionConfig, load_interaction_config
from agent.wechat_text import fit_wechat_message, to_wechat_plain_text
from agent.wechat_window import wechat_window_lock

ROBOT_PREFIX = "【YourSpecialJob"        # 推送摘要与互动回复统一前缀（方向识别依据）
REPLY_PREFIX = "【YourSpecialJob 回复】"
# 合并文本行首的时间前缀（"[11:00] 消息"），解析审批回复前需剥离
_TS_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")
APPROVE_WORDS = ("同意", "确认", "批准", "可以", "好的", "好", "yes", "y")
REJECT_WORDS = ("拒绝", "取消", "不行", "不要", "no")
SEND_COOLDOWN_SEC = 8
POLL_READ_TIMEOUT = 180
MAX_MESSAGES_PER_ROUND = 3
INTERACTION_LOCK_KEY = 0x59534A4D38
INTERACTION_REPLY_MAX_LEN = 1800

#1.微信对话长期记忆提取的后台任务强引用（防止被 GC）
_memory_tasks: set[asyncio.Task] = set()


class InteractionState:
    """互动 worker 内存状态（status/health 用）"""

    def __init__(self):
        self.running = False
        self.last_poll_at: str | None = None
        self.last_ok: bool | None = None
        self.last_error: str | None = None
        self.last_reply_at: float | None = None   # 单调时间（冷却）
        self.reply_times: list[float] = []         # 近 1 小时回复时间戳（限流）
        self.worker_pid = os.getpid()

    def can_reply(self, max_per_hour: int) -> bool:
        cutoff = time.time() - 3600
        self.reply_times = [t for t in self.reply_times if t >= cutoff]
        return len(self.reply_times) < max_per_hour

    def mark_replied(self) -> None:
        now = time.time()
        self.last_reply_at = now
        self.reply_times.append(now)

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "last_poll_at": self.last_poll_at,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "worker_pid": self.worker_pid,
        }


async def _load_config(pool) -> InteractionConfig:
    try:
        data = await get_raw_settings(pool)
        return load_interaction_config(data.get("preferences") or {})
    except Exception:
        logger.warning("读取互动配置失败，回退 .env", exc_info=True)
        return load_interaction_config(get_settings().preferences or {})


def _is_robot_text(text: str) -> bool:
    return str(text or "").strip().startswith(ROBOT_PREFIX)


def _parse_decision(text: str) -> str | None:
    """解析用户回复：approve / reject / None（只匹配开头，避免"你好"误判为同意）"""
    t = str(text or "").strip().lower()
    if not t:
        return None
    if any(t.startswith(w) for w in APPROVE_WORDS):
        return "approve"
    if any(t.startswith(w) for w in REJECT_WORDS):
        return "reject"
    return None


def _extract_interrupt(result: dict) -> dict | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None) or {}
    return value if isinstance(value, dict) else {}


def _extract_reply(result: dict) -> str:
    """取最后一条有文本的 AIMessage 内容"""
    messages = result.get("messages") or []
    for m in reversed(messages):
        msg_type = getattr(m, "type", None) or (m.get("type") if isinstance(m, dict) else None)
        if msg_type != "ai":
            continue
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = "\n".join(p for p in parts if p).strip()
            if text:
                return text
    return ""


def _extract_offer_list(result: dict) -> str:
    """取本轮 select_offers 工具返回的编号清单（模型常忘记复述，由后端兜底追加）"""
    for m in reversed(result.get("messages") or []):
        name = getattr(m, "name", None) or (m.get("name") if isinstance(m, dict) else None)
        if name != "select_offers":
            continue
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        text = str(content or "").strip()
        return text if "添加第 N 条" in text else ""
    return ""


def _with_offer_list(reply: str, result: dict) -> str:
    """回复未包含编号清单时自动追加，保证微信端一定能看到编号"""
    offer_text = _extract_offer_list(result)
    if not offer_text or "（id=" in reply:
        return reply
    return f"{reply.rstrip()}\n\n{offer_text}" if reply else offer_text


SEND_TOOL_NAME = "send_wechat_message_to_contact"


def build_interrupt_notice(value: dict) -> str:
    """把 HITL 中断转换为微信确认请求文本"""
    lines = ["【YourSpecialJob 审批请求】"]
    for action in value.get("action_requests", []):
        name = action.get("name")
        args = action.get("args") or {}
        #1.中介发送：展示联系人 + 消息预览（而不是原始 JSON）
        if name == SEND_TOOL_NAME:
            lines.append("操作：向微信联系人发送消息")
            lines.append(f"联系人：{args.get('contact_name') or '未填写'}")
            content = str(args.get("message") or "")
            lines.append(f"内容：{content[:600]}{'…' if len(content) > 600 else ''}")
        else:
            lines.append(f"操作：{name}")
            if args:
                lines.append(f"参数：{json.dumps(args, ensure_ascii=False)[:300]}")
    lines.append("回复「同意」执行，回复「拒绝」取消。")
    return "\n".join(lines)


def _schedule_memory_extract(store, thread_id: str, user_message: str, reply: str) -> None:
    """发送成功后 fire-and-forget 提取长期记忆（不阻塞互动轮询）"""
    if store is None or not user_message:
        return
    try:
        task = asyncio.create_task(
            extract_and_store_memory(store, thread_id, user_message, reply)
        )
        _memory_tasks.add(task)
        task.add_done_callback(_memory_tasks.discard)
    except Exception:
        logger.warning("启动微信长期记忆提取失败", exc_info=True)


async def _send_reply(
    pool, conversation: str, text: str, state: InteractionState,
    *, store=None, thread_id: str = "", user_message: str = "",
) -> bool:
    """发送互动回复（Markdown→纯文本 → 前缀 → 截断；失败重试一次）"""
    body = to_wechat_plain_text(text)
    message = fit_wechat_message(
        REPLY_PREFIX, body, INTERACTION_REPLY_MAX_LEN, suffix="\n…（已截断）"
    )
    for attempt in range(2):
        try:
            async with wechat_window_lock:
                await send_wechat_message(conversation, message)
            state.mark_replied()
            await touch_state(pool, conversation, replied=True)
            logger.info("互动回复已发送 conversation={} 长度={}", conversation, len(message))
            #2.长期记忆：用本轮用户消息 + 实际回复提取（后台执行，失败不影响回复）
            _schedule_memory_extract(store, thread_id, user_message, text)
            return True
        except WechatMcpError as e:
            logger.warning("互动回复发送失败（第 {} 次）：{}", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(3)
                continue
            state.last_error = str(e)[:300]
            await touch_state(pool, conversation, error=f"发送失败：{str(e)[:300]}")
            return False
    return False


async def _handle_pending(
    pool, agent, cfg: InteractionConfig, conversation: str, thread_id: str,
    pending: dict, merged_text: str, state: InteractionState, store=None,
) -> bool:
    """处理待审批：同意/拒绝 → resume；其他 → 提示。返回是否已处理（已回复）"""
    first_line = merged_text.splitlines()[0] if merged_text else ""
    decision = _parse_decision(_TS_PREFIX_RE.sub("", first_line))
    if decision is None:
        await _send_reply(
            pool, conversation,
            "有一项操作正在等待你确认：请回复「同意」执行，或回复「拒绝」取消。",
            state, store=store, thread_id=thread_id, user_message=merged_text,
        )
        return True

    #2.decisions 数量必须与挂起工具数一致（多工具同时中断时全部同意/全部拒绝）
    action_count = max(
        1, len((pending.get("interrupt_payload") or {}).get("action_requests") or [])
    )
    if decision == "approve":
        decisions = [{"type": "approve"} for _ in range(action_count)]
    else:
        decisions = [
            {"type": "reject", "message": "用户通过微信拒绝了该操作"}
            for _ in range(action_count)
        ]

    logger.info("互动审批恢复 thread={} decision={}", thread_id, decision)
    try:
        result = await agent.ainvoke(
            Command(resume={"decisions": decisions}),
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as e:
        logger.exception("互动审批恢复失败 thread={}", thread_id)
        await _send_reply(
            pool, conversation, f"审批恢复失败：{type(e).__name__}，请稍后重试。", state,
            store=store, thread_id=thread_id, user_message=merged_text,
        )
        return True

    await clear_pending(pool, thread_id)
    # 恢复后可能还有新的中断（多工具），继续保存
    interrupt_value = _extract_interrupt(result)
    if interrupt_value is not None:
        await save_pending(pool, thread_id, conversation, interrupt_value, cfg.pending_ttl_min)
        await _send_reply(
            pool, conversation, build_interrupt_notice(interrupt_value), state,
            store=store, thread_id=thread_id, user_message=merged_text,
        )
        return True

    reply = _extract_reply(result) or "操作已处理完成。"
    reply = _with_offer_list(reply, result)
    await _send_reply(
        pool, conversation, reply, state,
        store=store, thread_id=thread_id, user_message=merged_text,
    )
    return True


async def poll_interaction(
    pool, agent, cfg: InteractionConfig, state: InteractionState, *, store=None,
) -> dict:
    """执行一轮互动轮询（读取 → 去重 → pending/普通处理 → 回复）"""
    #1.微信访问总开关（设置页关闭时禁止操作微信）
    if not cfg.wechat_access_enabled:
        return {"skipped": True, "reason": "微信访问已关闭"}
    conversation = cfg.target
    if not conversation:
        return {"skipped": True, "reason": "未配置微信目标"}
    thread_id = f"wechat:{conversation}"

    #1.发送冷却：上次回复后 <8s 不读（防止微信 UI 状态异常）
    if state.last_reply_at and time.time() - state.last_reply_at < SEND_COOLDOWN_SEC:
        return {"skipped": True, "reason": "发送冷却中"}

    day = date.today()
    label = to_mcp_date(day)
    async with wechat_window_lock:
        messages = await call_wechat_get_history(
            conversation, label, allow_empty=True, timeout=POLL_READ_TIMEOUT
        )

    #2.过滤机器人消息（推送+回复）与空消息
    candidates = [
        m for m in messages
        if str(m.get("消息", "")).strip() and not _is_robot_text(m.get("消息", ""))
    ]

    #3.指纹去重 + 首轮基线
    await ensure_state(pool, conversation)
    state_row = await get_state(pool, conversation) or {}
    baseline = state_row.get("initialized_at") is None
    ledger_key = f"interaction:{conversation}"
    fp_by_message: dict[str, dict] = {}
    fps: list[str] = []
    for m in candidates:
        fp = message_fingerprint(ledger_key, day, m.get("发送者", ""), m.get("时间", ""), m.get("消息", ""))
        fp_by_message[fp] = m
        fps.append(fp)
    unseen = await filter_unseen(pool, ledger_key, day, fps)
    if baseline:
        await record_seen(pool, ledger_key, day, [
            {"fingerprint": fp, "sender": fp_by_message[fp].get("发送者"), "msg_time": fp_by_message[fp].get("时间")}
            for fp in fps
        ])
        await mark_initialized(pool, conversation)
        logger.info("互动首轮基线完成 conversation={} 记录 {} 条", conversation, len(fps))
        return {"baseline": True, "recorded": len(fps)}

    if not unseen:
        await touch_state(pool, conversation)
        return {"new_messages": 0}

    #4.合并新消息（每轮最多 3 条，其余留待下一轮）
    ordered = sorted(
        (fp_by_message[fp] for fp in unseen),
        key=lambda m: str(m.get("时间", "")),
    )
    batch = ordered[:MAX_MESSAGES_PER_ROUND]
    batch_ids = {id(m) for m in batch}
    merged_text = "\n".join(
        f"[{m.get('时间', '')}] {str(m.get('消息', '')).strip()}" for m in batch
    )

    #5.限流（审批回复不受限流影响，否则用户无法确认中介草稿）
    decision = _parse_decision(_TS_PREFIX_RE.sub("", merged_text.splitlines()[0] if merged_text else ""))
    if decision is None and not state.can_reply(cfg.max_replies_per_hour):
        await touch_state(pool, conversation, error="每小时回复上限已达，暂停互动")
        return {"skipped": True, "reason": "每小时回复上限"}

    #6.pending 优先
    pending = await get_pending(pool, thread_id)
    if pending is not None:
        handled = await _handle_pending(
            pool, agent, cfg, conversation, thread_id, pending, merged_text, state,
            store=store,
        )
        if handled:
            await record_seen(pool, ledger_key, day, [
                {"fingerprint": fp, "sender": fp_by_message[fp].get("发送者"),
                 "msg_time": fp_by_message[fp].get("时间")}
                for fp in unseen if id(fp_by_message[fp]) in batch_ids
            ])
        return {"pending": True}

    #6.5 中介待确认：用户回复「同意/拒绝」时路由到对应中介 thread（网页通道可能已抢先处理）
    if decision is not None:
        contact_pending = await get_contact_pending(pool)
        if contact_pending is not None:
            claimed = await claim_pending(pool, contact_pending["thread_id"])
            if claimed is not None:
                from agent.contact_interaction import resolve_contact_pending

                outcome = await resolve_contact_pending(
                    pool, agent, claimed, decision, pending_ttl_min=cfg.pending_ttl_min
                )
                await _send_reply(
                    pool, conversation, outcome.get("message") or "已处理。", state,
                    store=store, thread_id=thread_id, user_message=merged_text,
                )
                await record_seen(pool, ledger_key, day, [
                    {"fingerprint": fp, "sender": fp_by_message[fp].get("发送者"),
                     "msg_time": fp_by_message[fp].get("时间")}
                    for fp in unseen if id(fp_by_message[fp]) in batch_ids
                ])
                return {
                    "contact_pending": True,
                    "contact": outcome.get("contact_name"),
                    "ok": outcome.get("ok"),
                }
            logger.info("中介待确认已被其他通道处理，本轮按普通消息继续")

    #7.普通消息：唤醒 agent（不持窗口锁）
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": merged_text}]},
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as e:
        logger.exception("互动 agent 调用失败")
        await _send_reply(
            pool, conversation, f"处理失败：{type(e).__name__}，请稍后重试。", state,
            store=store, thread_id=thread_id, user_message=merged_text,
        )
        return {"error": f"{type(e).__name__}: {e}"}

    interrupt_value = _extract_interrupt(result)
    if interrupt_value is not None:
        await save_pending(pool, thread_id, conversation, interrupt_value, cfg.pending_ttl_min)
        await _send_reply(
            pool, conversation, build_interrupt_notice(interrupt_value), state,
            store=store, thread_id=thread_id, user_message=merged_text,
        )
    else:
        reply = _extract_reply(result)
        #3.模型常忘记复述编号清单：由后端追加（微信端必须能看到「添加第 N 条」）
        reply = _with_offer_list(reply, result)
        if not reply:
            reply = "模型未返回内容（可能是配额耗尽或配置无效），请稍后重试或到网页端操作。"
        await _send_reply(
            pool, conversation, reply, state,
            store=store, thread_id=thread_id, user_message=merged_text,
        )

    #8.记录已处理指纹（只记录本轮实际消费的前 N 条）
    await record_seen(pool, ledger_key, day, [
        {"fingerprint": fp, "sender": fp_by_message[fp].get("发送者"), "msg_time": fp_by_message[fp].get("时间")}
        for fp in unseen
        if id(fp_by_message[fp]) in batch_ids
    ])
    return {"replied": True, "messages": len(batch)}


async def run_poll(pool, agent, cfg: InteractionConfig, state: InteractionState,
                   *, store=None) -> dict:
    """带 advisory lock 的一轮互动（多实例互斥）"""
    if agent is None:
        return {"skipped": True, "reason": "agent 未就绪"}
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (INTERACTION_LOCK_KEY,))
        locked = (await cur.fetchone())[0]
        if not locked:
            return {"skipped": True, "reason": "其他实例运行中"}
        try:
            await cleanup_expired(pool)
            return await poll_interaction(pool, agent, cfg, state, store=store)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (INTERACTION_LOCK_KEY,))


async def interaction_loop(app) -> None:
    """固定节拍轮询循环（异常不退出）"""
    pool = await get_pool()
    state: InteractionState = app.state.interaction_state
    wakeup: asyncio.Event = app.state.interaction_wakeup
    logger.info("互动调度器已启动（pid={}，轮询模式）", state.worker_pid)

    while True:
        try:
            cfg = await _load_config(pool)
            wake_requested = wakeup.is_set()
            if wake_requested:
                wakeup.clear()
            #2.存在中介待确认时也轮询用户会话（微信端回复「同意/拒绝」需要被读取）
            contact_pending = False
            try:
                contact_pending = await get_contact_pending(pool) is not None
            except Exception:
                logger.warning("读取中介待确认失败", exc_info=True)
            #3.微信访问关闭时忽略手动触发（避免窗口被操作）
            if cfg.wechat_access_enabled and (cfg.enabled or wake_requested or contact_pending):
                state.running = True
                state.last_poll_at = datetime.now().astimezone().isoformat(timespec="seconds")
                try:
                    agent = getattr(app.state, "agent", None)
                    store = getattr(app.state, "store", None)
                    result = await run_poll(pool, agent, cfg, state, store=store)
                    state.last_ok = True
                    state.last_error = None
                    logger.info("互动轮询完成：{}", result)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    state.last_ok = False
                    state.last_error = f"{type(e).__name__}: {e}"[:300]
                    logger.exception("互动轮询异常")
                finally:
                    state.running = False
            timeout = cfg.interval_sec if cfg.enabled else 60
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=max(30, timeout))
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            logger.info("互动调度器已取消")
            raise
        except Exception:
            logger.exception("互动调度器异常，30 秒后重试")
            await asyncio.sleep(30)


def start_interaction(app) -> asyncio.Task:
    """lifespan 启动互动任务"""
    app.state.interaction_state = InteractionState()
    app.state.interaction_wakeup = asyncio.Event()
    task = asyncio.create_task(interaction_loop(app))
    app.state.interaction_task = task
    return task


async def stop_interaction(app) -> None:
    """lifespan 退出取消互动任务（最多等 10 秒）"""
    task = getattr(app.state, "interaction_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        logger.warning("互动任务未在 10 秒内退出")