# -*- coding: utf-8 -*-
"""
微信群消息摄入管道：读取 → 预过滤 → 分批 → 结构化 → 血缘校验 → 三层去重 → 入库。

双通道共用入口：前端按钮直调（backend 路由）与对话中 agent 工具都调用 ingest_group。
pywechat 会实际操纵微信窗口，模块级锁保证同一时刻只有一次读取。
"""
import asyncio
import re
import time
from datetime import date, datetime

from loguru import logger

from agent.commute_service import annotate_and_persist_commute
from agent.db.jobs_dao import insert_extracted
from agent.db.pool import get_pool
from agent.mcp_wechat import (
    call_wechat_get_history,
    combine_publish_time,
    find_source_ref,
    parse_mcp_date,
)
from agent.memory.struct_history import append_items, history_path, load_seen_hashes
from agent.models.job import (
    ExtractedBatch,
    IngestStats,
    JobExtractItem,
    compute_content_hash,
    normalize_text,
)
from agent.models_factory import get_extractor, get_llm_weak, is_model_configured
from agent.prompts.extract import build_batch_prompt
from agent.wechat_window import ingest_run_lock, wechat_window_lock

#1.常量与并发锁（窗口锁统一在 agent/wechat_window.py，发送与读取共用）
NOISE_EXACT = {"动画表情", "图片", "视频", "文件", "语音", "系统消息", "链接"}
MAX_CHARS_PER_BATCH = 3000   # 单批字符上限（拆小批次以并发提取，总耗时约减半）
MAX_COUNT_PER_BATCH = 20     # 单批条数上限（防短消息洪水）
MAX_CONCURRENT_BATCHES = 3   # 弱模型并发批数上限（防供应商限速）
_extract_sem = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

#2.模型可能把行首 [序号] 时刻 | 前缀一并回填进 raw_text，校验时剥离
_PREFIX_RE = re.compile(r"^\s*\[\d+\]\s*\d{1,2}:\d{2}\s*\|\s*")


def _prefilter(messages: list[dict]) -> tuple[list[dict], int, int]:
    """规则噪音过滤 + 原文去重（群内刷屏重复消息不送模型）

    Returns:
        (候选消息列表, 噪音条数, 原文重复条数)
    """
    seen: set[str] = set()
    candidates: list[dict] = []
    noise = 0
    dup_raw = 0
    for msg in messages:
        text = str(msg.get("消息", "")).strip()
        if not text or text in NOISE_EXACT:
            noise += 1
            continue
        key = normalize_text(text)
        if key in seen:
            dup_raw += 1
            continue
        seen.add(key)
        candidates.append(msg)
    return candidates, noise, dup_raw


def _chunk_messages(messages: list[dict]) -> list[list[dict]]:
    """按字符数为主、条数为上限切批（单条消息不跨批切割）"""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for msg in messages:
        text_len = len(str(msg.get("消息", "")))
        if current and (current_chars + text_len > MAX_CHARS_PER_BATCH
                        or len(current) >= MAX_COUNT_PER_BATCH):
            batches.append(current)
            current, current_chars = [], 0
        current.append(msg)
        current_chars += text_len
    if current:
        batches.append(current)
    return batches


async def _extract_batch(extractor, prompt: str, batch_no: int) -> ExtractedBatch | None:
    """单批结构化提取（tool-calling 失败时降级 json_mode；仍失败跳过该批）"""
    async with _extract_sem:  # 并发限流
        try:
            return await extractor.ainvoke(prompt)
        except Exception as e:
            logger.warning("第 {} 批结构化提取失败（tool-calling）：{}", batch_no, e)
        try:
            ext = get_llm_weak().with_structured_output(ExtractedBatch, method="json_mode")
            return await ext.ainvoke(prompt + "\n\n请只输出 JSON（包含 items 数组），不要其他文字。")
        except Exception as e:
            logger.error("第 {} 批 json_mode 降级也失败，跳过该批：{}", batch_no, e)
            return None


def _reconcile_raw_text(
    batch: list[dict], result: ExtractedBatch, fallback_date: date
) -> list[JobExtractItem]:
    """血缘校验：把模型回填的 raw_text 替换为批内权威原文，并合成 publish_time

    匹配不上（模型改写/截断）时保留模型版本并告警——该条可能逃过去重，但可观测。
    """
    by_index = {m.get("index"): m for m in batch}
    by_norm = {normalize_text(str(m.get("消息", ""))): m for m in batch}
    reconciled: list[JobExtractItem] = []
    for item in result.items:
        if not item.is_valid_job or item.job is None:
            continue
        raw = _PREFIX_RE.sub("", item.raw_text or "").strip()
        raw_norm = normalize_text(raw)
        #3.血缘回溯优先级：index 命中且文本一致 > raw_text 精确匹配 > index 兜底（模型改写 raw_text）
        cand = by_index.get(item.source_index) if item.source_index is not None else None
        msg = cand if cand is not None and normalize_text(str(cand.get("消息", ""))) == raw_norm else None
        if msg is None:
            msg = by_norm.get(raw_norm)
        if msg is None and cand is not None:
            logger.warning("raw_text 与 source_index 文本不一致，按序号兜底：index={} {}", item.source_index, raw[:60])
            msg = cand
        if msg is None:
            logger.warning("source_index/raw_text 均无法匹配原文（模型改写？）：index={} {}",
                           item.source_index, raw[:60])
            reconciled.append(item.model_copy(update={"raw_text": raw}))
            continue
        job = item.job
        # 发布时间以微信消息的原始时刻为准重新合成（模型可能把本地时间误标为 UTC "Z"，
        # 会导致入库后显示 +8 小时）；消息时刻无法解析时才保留模型值
        pub = combine_publish_time(msg.get("时间"), fallback_date)
        if pub is not None:
            job = job.model_copy(update={"publish_time": pub})
        reconciled.append(item.model_copy(update={"raw_text": str(msg.get("消息", "")).strip(), "job": job}))
    return reconciled


async def ingest_messages(
    group_name: str,
    target_date_label: str,
    target_d: date,
    messages: list[dict],
    source: str,
    source_ref: str,
    progress_cb=None,
    started_at: float | None = None,
) -> IngestStats:
    """公共摄入管道：预过滤 → 分批 → 结构化 → 血缘校验 → 三层去重 → 入库

    不含微信读取与窗口锁：自动通道（wechat_auto）与手动通道（manual_text/manual_image）共用。

    Args:
        group_name: 群名（去重与血缘用）
        target_date_label: 日期标签（写入统计，如 "26/11/3" 或 "2026-11-03"）
        target_d: 目标日期的 date 对象（缺省工作日期与发布时间合成用）
        messages: 消息字典列表（index/发送者/时间/消息）
        source: 血缘来源（wechat_auto / manual_text / manual_image）
        source_ref: 血缘引用（导出文件名 / 手动粘贴标识 / 上传文件名）
        progress_cb: 进度回调（可选）
        started_at: 计时起点（自动通道从读取前开始计时；缺省从本函数开始）

    Returns:
        摄入统计 IngestStats
    """
    t0 = started_at if started_at is not None else time.time()

    def report(message: str) -> None:
        logger.info(message)
        if progress_cb is not None:
            try:
                progress_cb(message)
            except Exception:  # 进度上报失败不影响主流程
                pass

    #4.预过滤：噪音 + 原文刷屏重复
    candidates, noise, dup_raw = _prefilter(messages)
    batches = _chunk_messages(candidates)
    report(f"已读取 {len(messages)} 条消息，过滤后 {len(candidates)} 条候选，"
           f"分 {len(batches)} 批并发提取中…")

    #5.并发结构化（限流 3） + 血缘校验
    extractor = get_extractor()
    prompts = [
        build_batch_prompt(
            group_name=group_name,
            target_date=target_date_label,
            target_date_cn=target_d.isoformat(),
            work_date=target_d.isoformat(),
            batch_idx=idx,
            total_batches=len(batches),
            messages=batch,
        )
        for idx, batch in enumerate(batches, 1)
    ]
    results = await asyncio.gather(
        *[_extract_batch(extractor, prompt, i) for i, prompt in enumerate(prompts, 1)]
    )
    valid_items: list[JobExtractItem] = []
    for batch, result in zip(batches, results):
        if result is not None:
            valid_items.extend(_reconcile_raw_text(batch, result, target_d))
    llm_valid = len(valid_items)
    report(f"结构化完成：有效兼职 {llm_valid} 条，正在去重入库…")

    #6.第一层：run 内 content_hash 去重（模型跨批重复输出）
    seen_run: set[str] = set()
    unique: list[tuple[str, JobExtractItem]] = []
    dup_in_run = 0
    for item in valid_items:
        h = compute_content_hash(group_name, item.raw_text)
        if h in seen_run:
            dup_in_run += 1
            continue
        seen_run.add(h)
        unique.append((h, item))

    #7.第二层：jsonl 已见 hash（按 source 分文件，互不污染去重缓存）
    path = history_path(group_name, source)
    seen_jsonl = await load_seen_hashes(path)
    new_items: list[JobExtractItem] = []
    to_store: list[dict] = []
    dup_jsonl = 0
    for h, item in unique:
        if h in seen_jsonl:
            dup_jsonl += 1
            continue
        new_items.append(item)
        to_store.append({
            "content_hash": h,
            "group_name": group_name,
            "source": source,
            "source_ref": source_ref,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "extracted": item.model_dump(mode="json"),
        })

    #8.第三层：数据库 UNIQUE 兜底；DB 成功后再写 jsonl（避免缓存与库不一致）
    pool = await get_pool()
    new_inserted, dup_db, inserted_ids = await insert_extracted(
        pool, group_name, new_items, source_ref, target_d, source=source, with_ids=True
    )
    await append_items(path, to_store)

    #9.通勤入库（高德 MCP；失败不阻塞入库，仅计数提示）
    commute_failed = 0
    if inserted_ids:
        commute_rows = [
            {"id": jid, "location": (item.job.location if item.job else None)}
            for jid, item in zip(inserted_ids, new_items)
        ]
        try:
            ok_count, mcp_failed = await annotate_and_persist_commute(pool, commute_rows)
            commute_failed = (len(inserted_ids) - ok_count) if not mcp_failed else len(inserted_ids)
        except Exception:
            logger.exception("通勤计算异常（不影响入库）")
            commute_failed = len(inserted_ids)

    stats = IngestStats(
        group_name=group_name,
        target_date=target_date_label,
        source_ref=source_ref,
        total_messages=len(messages),
        noise_filtered=noise,
        dup_raw=dup_raw,
        batches=len(batches),
        llm_valid=llm_valid,
        new_inserted=new_inserted,
        dup_in_run=dup_in_run,
        dup_jsonl=dup_jsonl,
        dup_db=dup_db,
        commute_failed=commute_failed,
        elapsed_sec=round(time.time() - t0, 1),
    )
    logger.info("摄入完成：{}", stats.model_dump())
    return stats


async def ingest_group(
    group_name: str, target_date: str, progress_cb=None
) -> IngestStats:
    """读取指定微信群在指定日期的消息，结构化后入库（自动通道入口）

    Args:
        group_name: 群名，如 "兼职1群"
        target_date: YY/M/D 格式日期，如 "26/11/3"
        progress_cb: 进度回调（可选，SSE 通道用 get_stream_writer 转发到前端）

    Returns:
        摄入统计 IngestStats

    Raises:
        RuntimeError: 模型未配置 / 微信读取失败（消息为中文降级提示）
    """
    t0 = time.time()

    def report(message: str) -> None:
        logger.info(message)
        if progress_cb is not None:
            try:
                progress_cb(message)
            except Exception:  # 进度上报失败不影响主流程
                pass

    #3.串行化：ingest_run_lock 防重复提取；窗口锁只覆盖实际读取微信窗口的时段
    async with ingest_run_lock:
        if not is_model_configured("weak"):
            raise RuntimeError("弱模型未配置，请先在 settings/.env 中填写弱模型 API key")

        target_d = parse_mcp_date(target_date)
        report(f"正在打开微信读取 {group_name} {target_date} 的聊天记录（请勿操作微信）…")
        try:
            async with wechat_window_lock:
                messages = await call_wechat_get_history(group_name, target_date)
        except Exception as e:
            raise RuntimeError(f"微信读取失败：{e}。请确认 PC 微信已登录，或改用手动粘贴。") from e

        source_ref = find_source_ref(group_name)
        logger.info("读取到 {} 条消息（{} / {}）", len(messages), group_name, target_date)

        return await ingest_messages(
            group_name=group_name,
            target_date_label=target_date,
            target_d=target_d,
            messages=messages,
            source="wechat_auto",
            source_ref=source_ref,
            progress_cb=progress_cb,
            started_at=t0,
        )