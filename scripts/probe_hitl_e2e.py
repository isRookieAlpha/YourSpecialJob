# -*- coding: utf-8 -*-
"""
HITL + PDF 端到端验证（真实 agent 工具链，InMemorySaver，不碰微信）。

流程：发"导出报告"消息 → SSE 事件流 → hitl 事件 → approve 续流 → export_job_report
真实执行 → 检查 backend/reports/ 生成 PDF。

用法：python scripts/probe_hitl_e2e.py
"""
import asyncio
import json
import sys
from pathlib import Path

# psycopg 3.x 不支持 Windows ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.graph import build_agent  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402
from agent.report_pdf import REPORT_DIR  # noqa: E402
from backend.sse import sse_events  # noqa: E402


async def consume(events, label: str) -> dict | None:
    """消费 SSE 事件流并打印，返回最后一个 hitl 事件（若有）"""
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    hitl = None
    async for item in events:
        event, data = item["event"], json.loads(item["data"])
        if event == "token":
            print(f"[token] {data['content'][:60]!r}")
        elif event == "hitl":
            hitl = data
            print(f"[hitl] actions={data['actions']} allowed={data['allowed']}")
        elif event == "tool":
            print(f"[tool] {data['name']}: {data['preview'][:120]}")
        else:
            print(f"[{event}] {data}")
    return hitl


async def main() -> int:
    if not is_model_configured("strong"):
        print("请先在 agent/.env 填写 STRONG 模型配置")
        return 2

    before = set(REPORT_DIR.glob("*.pdf")) if REPORT_DIR.exists() else set()

    agent = build_agent(checkpointer=InMemorySaver())
    thread_id = "probe-hitl-e2e"
    message = "请导出2026年11月1日到11月10日的兼职报告，标题「11月上旬兼职」"

    hitl = await consume(
        sse_events(agent, thread_id, {"messages": [{"role": "user", "content": message}]}),
        "第 1 段：请求导出报告 → 预期 hitl 事件",
    )
    if not hitl:
        print("!! 未收到 hitl 事件（模型可能没有调用 export_job_report，检查 system prompt）")
        return 1

    await consume(
        sse_events(agent, thread_id, Command(resume={"decisions": [{"type": "approve"}]})),
        "第 2 段：approve 恢复 → 预期工具执行 + PDF 生成 + 回复链接",
    )

    after = set(REPORT_DIR.glob("*.pdf")) if REPORT_DIR.exists() else set()
    new_files = after - before
    print(f"\n新生成的 PDF：{[p.name for p in new_files] or '无'}")
    if not new_files:
        print("!! 未生成 PDF")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))