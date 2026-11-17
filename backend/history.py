# -*- coding: utf-8 -*-
"""
历史消息转换：langchain checkpointer 消息 → 前端可渲染格式。

probe_hitl.py 实测（2026-11-04，qwen3.5-omni-plus + InMemorySaver）：
- aget_state(config).values["messages"] 包含完整对话历史
- HumanMessage(content=str) → 用户消息
- AIMessage(content="", tool_calls=[...]) → 工具调用请求（前端不显示）
- AIMessage(content=str) → 助手回复
- ToolMessage(content=str, name=str) → 工具执行结果
- SummarizationMiddleware 触发后旧消息被替换为摘要（类型仍为 AIMessage/HumanMessage，
  通过 response_metadata 或 additional_kwargs 中的特征识别；识别失败退化为普通消息显示）
"""
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def _extract_text(content) -> str:
    """从消息 content 提取纯文本（兼容 str 与多模态 list 块）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 多模态块：[{"type": "text", "text": "..."}, {"type": "image_url", ...}]
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def _is_summarization_message(msg: BaseMessage) -> bool:
    """识别 SummarizationMiddleware 生成的摘要消息

    摘要消息特征（probe 实测 + 源码推断）：
    - response_metadata 或 additional_kwargs 中可能含 summarization 相关标记
    - 保守策略：识别失败时退化为普通消息（不丢数据）
    """
    # 检查 response_metadata 中的模型名或特殊标记
    meta = getattr(msg, "response_metadata", None) or {}
    kwargs = getattr(msg, "additional_kwargs", None) or {}

    # SummarizationMiddleware 使用弱模型生成摘要，可能带特定标记
    # 保守检测：如果 content 以典型摘要前缀开头且消息较长，可能是摘要
    # 但这不可靠，暂时返回 False，让摘要作为普通 assistant 消息显示
    # TODO: probe 长对话触发 Summarization 后补充精确特征
    return False


def convert_history_messages(messages: list[BaseMessage]) -> list[dict]:
    """将 langchain 消息列表转换为前端格式

    Args:
        messages: aget_state().values["messages"] 返回的消息列表

    Returns:
        前端可渲染的消息字典列表：
        - {"role": "user", "content": str}
        - {"role": "assistant", "content": str}
        - {"role": "tool", "name": str, "content": str}  (preview[:300])
        - {"role": "system_note", "content": str}  (摘要消息)
    """
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            text = _extract_text(msg.content)
            if text.strip():
                result.append({"role": "user", "content": text})

        elif isinstance(msg, AIMessage):
            # 空 content + tool_calls → 工具调用请求，前端不显示
            if not msg.content and getattr(msg, "tool_calls", None):
                continue
            text = _extract_text(msg.content)
            if not text.strip():
                continue
            # 检查是否为摘要消息
            if _is_summarization_message(msg):
                result.append({"role": "system_note", "content": "此前对话已摘要"})
            else:
                result.append({"role": "assistant", "content": text})

        elif isinstance(msg, ToolMessage):
            # 工具执行结果，对齐 SSE tool 事件的 preview[:300]
            preview = str(msg.content)[:300]
            result.append({
                "role": "tool",
                "name": getattr(msg, "name", "tool"),
                "content": preview,
            })

    return result


def convert_history_todos(values: dict) -> list[dict]:
    """从 state values 提取 todos（恢复 TodoProgress 步骤条）

    Args:
        values: aget_state().values

    Returns:
        todos 列表 [{"content": str, "status": str}]
    """
    todos = values.get("todos")
    if not todos:
        return []
    return [
        {"content": t.get("content", ""), "status": t.get("status", "pending")}
        for t in todos
    ]
