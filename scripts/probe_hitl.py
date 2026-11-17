# -*- coding: utf-8 -*-
"""
HITL 中断/恢复事件形状探测（真实模型，InMemorySaver）。

验证结论（写入 backend/sse.py 与 backend/history.py 注释）：
1. `__interrupt__` 在 astream(["messages","updates","custom"]) 下出现的位置与 payload 形状
2. approve / reject 后 `Command(resume=)` 续流的事件序列
3. 中断后 aget_state 的 interrupts / values["messages"] 形态（摘要消息特征供历史回放过滤）

用法：python scripts/probe_hitl.py（需 agent/.env 已配置 STRONG 模型）
"""
import asyncio
import sys
from pathlib import Path

# psycopg 3.x 不支持 Windows ProactorEventLoop：主循环统一用 Selector
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import (  # noqa: E402
    HumanInTheLoopMiddleware,
    TodoListMiddleware,
)
from langchain_core.tools import tool  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.models_factory import get_llm_strong, is_model_configured  # noqa: E402


@tool(parse_docstring=True)
def dummy_export(title: str) -> str:
    """导出兼职报告（探针用假工具，模拟 export_job_report 的 HITL 拦截）

    Args:
        title: 报告标题

    Returns:
        导出结果文本
    """
    return f"已导出报告：{title}（共 3 条记录，下载链接 /api/reports/dummy.pdf）"


def build_probe_agent(checkpointer):
    """探针专用 agent：假工具 + HITL 中间件（与 M2 graph.py 目标形态一致）"""
    return create_agent(
        model=get_llm_strong(),
        tools=[dummy_export],
        middleware=[
            TodoListMiddleware(),
            HumanInTheLoopMiddleware(
                interrupt_on={"dummy_export": {"allowed_decisions": ["approve", "edit", "reject"]}},
                description_prefix="工具执行需要审批",
            ),
        ],
        system_prompt="当用户要求导出/生成报告时，调用 dummy_export 工具。用中文回答。",
        checkpointer=checkpointer,
    )


def dump_payload(mode: str, payload, depth: int = 0) -> None:
    """递归打印 payload 形状（类型 + 关键字段），不打印完整内容"""
    indent = "  " * depth
    if isinstance(payload, dict):
        for k, v in payload.items():
            print(f"{indent}[{mode}] dict key={k!r} value_type={type(v).__name__}")
            if k in ("__interrupt__", "interrupts") or depth < 2:
                dump_payload(mode, v, depth + 1)
    elif isinstance(payload, (list, tuple)):
        print(f"{indent}[{mode}] {type(payload).__name__} len={len(payload)}")
        for i, item in enumerate(payload[:3]):
            print(f"{indent}  [{i}] {type(item).__name__}")
            # Interrupt 对象打印 value/id
            if hasattr(item, "value") and hasattr(item, "id"):
                print(f"{indent}      id={item.id!r}")
                print(f"{indent}      value_type={type(item.value).__name__}")
                dump_payload(mode, item.value, depth + 3)
            elif depth < 3:
                dump_payload(mode, item, depth + 2)
    elif hasattr(payload, "value") and hasattr(payload, "id"):
        # Interrupt 对象
        print(f"{indent}[{mode}] Interrupt id={payload.id!r} value_type={type(payload.value).__name__}")
        dump_payload(mode, payload.value, depth + 1)
    else:
        s = str(payload)
        print(f"{indent}[{mode}] {type(payload).__name__} = {s[:120]!r}")


async def stream_and_collect(agent, config, inputs, label: str) -> None:
    """流式执行并打印全部事件形状"""
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    async for mode, payload in agent.astream(
        inputs,
        config=config,
        stream_mode=["messages", "updates", "custom"],
    ):
        if mode == "messages":
            if isinstance(payload, tuple) and len(payload) == 2:
                chunk, metadata = payload
                node = (metadata or {}).get("langgraph_node")
                content = getattr(chunk, "content", "")
                if content:
                    print(f"[messages] {type(chunk).__name__} node={node} content={str(content)[:60]!r}")
                else:
                    print(f"[messages] {type(chunk).__name__} node={node} content=（空）")
        else:
            print(f"[{mode}] payload_type={type(payload).__name__}")
            dump_payload(mode, payload)


async def inspect_state(agent, config, label: str) -> None:
    """打印 aget_state 的 messages / interrupts / todos 形态"""
    print(f"\n{'-' * 70}\naget_state 检查：{label}\n{'-' * 70}")
    snapshot = await agent.aget_state(config)
    print(f"next={snapshot.next}")
    print(f"interrupts={snapshot.interrupts!r}")
    values = snapshot.values or {}
    print(f"values keys={list(values.keys())}")
    messages = values.get("messages", [])
    print(f"messages 共 {len(messages)} 条：")
    for i, m in enumerate(messages):
        content = getattr(m, "content", "")
        extra = ""
        if hasattr(m, "tool_calls") and m.tool_calls:
            extra = f" tool_calls={[tc.get('name') for tc in m.tool_calls]}"
        # 摘要消息特征探测：额外字段
        rm_fields = [k for k in ("additional_kwargs", "response_metadata") if getattr(m, k, None)]
        print(f"  [{i}] {type(m).__name__} content_type={type(content).__name__} "
              f"len={len(str(content))}{extra} meta_fields={rm_fields}")
        if isinstance(content, list):
            print(f"      content 块类型: {[b.get('type') if isinstance(b, dict) else type(b).__name__ for b in content]}")
    todos = values.get("todos")
    if todos is not None:
        print(f"todos={todos}")


async def main() -> int:
    if not is_model_configured("strong"):
        print("请先在 agent/.env 填写 STRONG 模型配置")
        return 2

    checkpointer = InMemorySaver()
    agent = build_probe_agent(checkpointer)
    thread_id = "probe-hitl-1"
    config = {"configurable": {"thread_id": thread_id}}

    #1.触发 HITL 中断：观察 __interrupt__ 在 updates 通道的形状
    await stream_and_collect(
        agent, config,
        {"messages": [{"role": "user", "content": "帮我导出11月上旬的兼职报告，标题就叫「11月兼职」"}]},
        "第 1 段：触发 dummy_export → 预期 HITL 中断",
    )

    #2.中断后检查 state：interrupts 与 messages 形态
    await inspect_state(agent, config, "中断挂起时")

    #3.approve 恢复：观察续流事件序列（工具真正执行 → 最终回复）
    await stream_and_collect(
        agent, config,
        Command(resume={"decisions": [{"type": "approve"}]}),
        "第 2 段：approve 恢复 → 预期工具执行 + 最终回复",
    )

    #4.第二个线程测 reject：观察拒绝后的事件序列
    thread_id2 = "probe-hitl-2"
    config2 = {"configurable": {"thread_id": thread_id2}}
    await stream_and_collect(
        agent, config2,
        {"messages": [{"role": "user", "content": "导出报告，标题「测试」"}]},
        "第 3 段（新线程）：触发中断",
    )
    await stream_and_collect(
        agent, config2,
        Command(resume={"decisions": [{"type": "reject", "message": "暂时不需要导出"}]}),
        "第 4 段：reject 恢复 → 预期 agent 解释已取消",
    )

    #5.最终 state 检查：完整对话的 messages 类型序列（供历史回放 convert 参考）
    await inspect_state(agent, config, "approve 完成后")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
