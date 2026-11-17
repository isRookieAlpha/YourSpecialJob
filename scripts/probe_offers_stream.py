# -*- coding: utf-8 -*-
"""
offers custom 事件探针：真实 agent 推荐 → 验证 SSE offers 事件结构。

用法：python scripts/probe_offers_stream.py
"""
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.graph import build_agent  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402
from backend.sse import sse_events  # noqa: E402


async def main() -> int:
    if not is_model_configured("strong"):
        print("请先在 agent/.env 填写 STRONG 模型配置")
        return 2

    agent = build_agent(checkpointer=InMemorySaver())
    message = "帮我找佣金100元以上的兼职，直接查数据库"
    print(f"{'=' * 70}\n输入：{message}\n{'=' * 70}")

    offers = None
    async for item in sse_events(agent, "probe-offers", {"messages": [{"role": "user", "content": message}]}):
        event, data = item["event"], json.loads(item["data"])
        if event == "offers":
            offers = data["items"]
            print(f"[offers] 收到 {len(offers)} 条候选")
            for it in offers[:3]:
                print(f"  id={it['id']} {it['work_summary']} | {it['source_label']} | status={it['offer_status']}")
        elif event in ("tool", "error"):
            print(f"[{event}] {str(data)[:150]}")

    if not offers:
        print("\n未收到 offers 事件（可能无候选数据或模型未调用 query_jobs）")
        return 1
    #1.结构断言
    required = {"id", "work_summary", "source_label", "offer_status", "ingested_at"}
    assert required <= set(offers[0]), f"缺字段：{required - set(offers[0])}"
    assert "raw_text" not in offers[0]
    print("\noffers 事件结构验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))