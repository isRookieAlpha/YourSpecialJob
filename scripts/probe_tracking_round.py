# -*- coding: utf-8 -*-
"""
跟踪轮次探针：真实读取微信 → 增量入库 → 生成推送摘要。

用法：
    python scripts/probe_tracking_round.py              # dry-run：读取+入库+打印摘要，不发送
    python scripts/probe_tracking_round.py --send       # 真实发送到 WECHAT_SENDER_ID
    python scripts/probe_tracking_round.py --group 兼职2群
    python scripts/probe_tracking_round.py --no-ingest  # 只读不送模型（跳过入库与摘要）

注意：会实际操作微信窗口（打开聊天记录翻页），执行期间请不要操作微信。
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import setup_logging  # noqa: E402
from agent.db.pool import close_pool, get_pool  # noqa: E402
from agent.tracking import (  # noqa: E402
    TrackingState,
    load_config,
    process_group,
    push_round,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="跟踪轮次探针")
    parser.add_argument("--send", action="store_true", help="真实发送推送摘要")
    parser.add_argument("--group", default=None, help="只处理指定群（覆盖配置）")
    parser.add_argument("--no-ingest", action="store_true", help="只读取，不入库（调试用）")
    args = parser.parse_args()

    setup_logging()
    pool = await get_pool()
    cfg = await load_config(pool)
    if args.group:
        cfg.groups = [args.group]
    if not cfg.groups:
        print("未配置跟踪群（设置页 → 长期跟踪，或用 --group 指定）")
        await close_pool()
        return 2

    print(f"跟踪群：{cfg.groups} | 间隔：{cfg.interval_min} 分钟 | 推送：{cfg.push_enabled}")
    if args.no_ingest:
        print("（--no-ingest 模式：不入库、不生成摘要）")
        await close_pool()
        return 0

    state = TrackingState()
    round_start = datetime.now().astimezone()
    for group in cfg.groups:
        print(f"\n===== 处理群 {group}（请勿操作微信）=====")
        stats = await process_group(pool, cfg, group, round_start, state)
        print(f"结果：{stats}")

    result = await push_round(pool, cfg, state, dry_run=not args.send)
    if result.get("dry_run"):
        print(f"\n===== 推送摘要预览（未发送，候选 {result['candidates']} 条）=====")
        print(result.get("summary") or "（无候选）")
        if not args.send:
            print("\n如需真实发送，加 --send 参数")
    else:
        print(f"\n推送结果：{result}")

    await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))