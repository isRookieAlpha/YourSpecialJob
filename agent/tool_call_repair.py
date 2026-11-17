# -*- coding: utf-8 -*-
"""工具调用参数修复中间件。

MiniMax-M2.5 等模型偶尔生成非法 JSON 参数（如中文字符串未加引号：
'{"group_name": 兼职1群}'），LangChain 会将其放入 invalid_tool_calls 并忽略，
agent 因此认为没有工具调用而直接结束（表现为只回一句"我来读取…"但什么也不做）。

本中间件对解析失败的参数做宽松修复（给冒号后的裸值补引号），修复成功则转回
tool_calls 正常执行。必须放在 HumanInTheLoopMiddleware 之后（最内层），
保证修复后的发送类工具调用仍会被 HITL 拦截。
"""
import json
import re
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

#1.冒号后不带引号的裸值：排除已加引号、对象/数组起始、空白与逗号
_BARE_VALUE = re.compile(r'(:\s*)([^"{}\[\],\s][^,}\]]*)')
_NUMBER_OR_LITERAL = re.compile(r'-?\d+(?:\.\d+)?|true|false|null')


def repair_json_arguments(raw: str) -> dict | None:
    """尝试把工具参数修复为 dict；无法修复返回 None。

    Args:
        raw: 模型输出的原始参数字符串
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    #2.给未加引号的裸值补引号（数字/布尔/null 保持原样）
    def quote(match: re.Match) -> str:
        prefix, value = match.group(1), match.group(2).strip()
        if _NUMBER_OR_LITERAL.fullmatch(value):
            return prefix + value
        return f'{prefix}"{value}"'

    try:
        value = json.loads(_BARE_VALUE.sub(quote, text))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def repair_invalid_tool_calls(msg: AIMessage) -> AIMessage:
    """把消息里可修复的 invalid_tool_calls 转为 tool_calls（不可修复的保留）。"""
    invalid = getattr(msg, "invalid_tool_calls", None) or []
    if not invalid:
        return msg

    tool_calls = list(msg.tool_calls or [])
    still_invalid = []
    repaired_count = 0
    for bad in invalid:
        args = repair_json_arguments(bad.get("args") or "")
        if args is None:
            still_invalid.append(bad)
            continue
        tool_calls.append({
            "name": bad.get("name") or "",
            "args": args,
            "id": bad.get("id") or f"repaired-{uuid4().hex[:12]}",
            "type": "tool_call",
        })
        repaired_count += 1

    if not repaired_count:
        return msg
    return msg.model_copy(update={"tool_calls": tool_calls, "invalid_tool_calls": still_invalid})


class ToolCallRepairMiddleware(AgentMiddleware):
    """模型响应兜底：修复非法 JSON 工具参数，避免整轮任务静默中止。"""

    def wrap_model_call(self, request, handler):
        response = handler(request)
        response.result = [repair_invalid_tool_calls(m) for m in response.result]
        return response

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        response.result = [repair_invalid_tool_calls(m) for m in response.result]
        return response
