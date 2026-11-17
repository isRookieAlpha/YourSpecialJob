# -*- coding: utf-8 -*-
"""
手动粘贴端到端验证（真实 agent + 真实弱模型 + 真实测试库）。

流程：发送"粘贴文本入库"消息 → agent 调 ingest_text_messages → 入库 → 回复统计。
测试群名固定为「手动粘贴测试」，结束后自动清理该群测试数据。

用法：python scripts/probe_manual_e2e.py
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

from agent.db.pool import get_pool  # noqa: E402
from agent.graph import build_agent  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402
from backend.sse import sse_events  # noqa: E402

TEST_GROUP = "手动粘贴测试"
PASTED = """张姐 2026-11-03 10:15
酒店宴会传菜日结110，7:30-21:00，静安寺，加微信abc123

李哥 2026-11-03 10:20
仓库搬货200一天，松江，男生优先，联系13800000000"""


async def cleanup(pool) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM jobs WHERE group_name = %s", (TEST_GROUP,))


async def main() -> int:
    if not is_model_configured("strong") or not is_model_configured("weak"):
        print("请先在 agent/.env 填写 STRONG/WEAK 模型配置")
        return 2

    pool = await get_pool()
    await cleanup(pool)  # 预清理，保证从零开始

    agent = build_agent(checkpointer=InMemorySaver())
    message = (
        f"以下是我从微信复制的群消息，帮我入库（群名：{TEST_GROUP}，日期 2026-11-03）：\n\n{PASTED}"
    )
    print(f"{'=' * 70}\n发送消息（含粘贴文本）\n{'=' * 70}")

    saw_tool = False
    content = ""
    async for item in sse_events(agent, "probe-manual-e2e", {"messages": [{"role": "user", "content": message}]}):
        event, data = item["event"], json.loads(item["data"])
        if event == "tool" and data["name"] == "ingest_text_messages":
            saw_tool = True
            print(f"[tool] ingest_text_messages: {data['preview'][:200]}")
        elif event == "token":
            content += data["content"]
        elif event in ("progress", "error"):
            print(f"[{event}] {data}")

    print(f"\n最终回复：{content[:300]}")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT source, source_ref, work_summary, commission_min FROM jobs WHERE group_name = %s ORDER BY id",
            (TEST_GROUP,),
        )
        rows = await cur.fetchall()
    print(f"\n数据库中测试群记录（{len(rows)} 条）：")
    for r in rows:
        print(f"  source={r[0]} source_ref={r[1]} summary={r[2]} commission={r[3]}")

    ok = saw_tool and len(rows) >= 2 and all(r[0] == "manual_text" for r in rows)
    await cleanup(pool)  # 清理测试数据
    print(f"\n测试数据已清理。结果：{'通过' if ok else '失败'}（工具调用={saw_tool}, 记录数={len(rows)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))