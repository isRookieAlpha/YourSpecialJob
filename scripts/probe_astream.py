# -*- coding: utf-8 -*-
"""
astream(["messages","updates"]) 事件形状探测（真实模型，InMemorySaver）。

验证结论（写入 backend/sse.py 注释）：
1. yield 值是否 (mode, payload) 二元组；messages 模式 payload 是否 (chunk, metadata)
2. token 增量出现在哪些 langgraph_node
3. todos 增量出现在哪个节点、键名形状
4. ToolMessage 是否只出现在 updates 的 tools 节点
"""
import asyncio
import sys
from pathlib import Path

# psycopg 3.x 不支持 Windows ProactorEventLoop：主循环统一用 Selector（微信 MCP 在独立线程用 Proactor）
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessageChunk, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.graph import build_agent  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402


async def run_once(agent, thread_id: str, message: str) -> None:
    """发一条消息并逐事件打印形状"""
    print(f"\n{'=' * 70}\n输入: {message}\n{'=' * 70}")
    config = {"configurable": {"thread_id": thread_id}}
    token_count = 0
    async for mode, payload in agent.astream(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            # 预期 payload = (chunk, metadata)
            if isinstance(payload, tuple) and len(payload) == 2:
                msg, metadata = payload
                node = metadata.get("langgraph_node")
                if isinstance(msg, AIMessageChunk) and msg.content:
                    token_count += 1
                    if token_count <= 3 or token_count % 50 == 0:
                        print(f"[messages] AIMessageChunk node={node} content={msg.content[:40]!r}")
                elif isinstance(msg, ToolMessage):
                    print(f"[messages] ToolMessage node={node} name={msg.name} content={str(msg.content)[:60]!r}")
                elif isinstance(msg, AIMessageChunk):
                    pass  # 空 content 的 tool_call 增量
            else:
                print(f"[messages] 非二元组形状: {type(payload)}")
        else:  # updates
            for node, delta in payload.items():
                if not isinstance(delta, dict):
                    print(f"[updates] node={node} delta_type={type(delta).__name__}（非 dict，跳过扫描）")
                    continue
                keys = list(delta.keys())
                print(f"[updates] node={node} keys={keys}")
                if "todos" in delta and delta["todos"] is not None:
                    print(f"    todos = {delta['todos']}")
                for m in delta.get("messages", []) or []:
                    if isinstance(m, ToolMessage):
                        print(f"    ToolMessage name={m.name} content={str(m.content)[:80]!r}")
                    elif isinstance(m, AIMessageChunk):
                        print(f"    AIMessageChunk content={str(m.content)[:40]!r}")
    print(f"\n[messages] token 事件总数: {token_count}")


async def main() -> int:
    if not is_model_configured("strong") or not is_model_configured("weak"):
        print("请先在 agent/.env 填写 STRONG/WEAK 模型配置")
        return 2

    agent = build_agent(checkpointer=InMemorySaver())

    #1.简单对话（不触发工具）：验证 token 流与节点名
    await run_once(agent, "probe-simple", "你好，用一句话介绍你能做什么")

    #2.触发工具（query_jobs，DB 可能为空但工具会执行）：验证 tools 节点 ToolMessage
    await run_once(agent, "probe-tool", "帮我查一下 2026-11-05 有没有兼职，直接查数据库")

    #3.触发 todo 中间件：验证 todos 增量出现的节点与键形状
    await run_once(agent, "probe-todo", "我要找兼职，请先用 todo 工具列出你的工作步骤，再简单说明")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))