# -*- coding: utf-8 -*-
"""
调试：打印 wechat_get_chat_history 的原始返回结构（不打印消息全文，只看类型/键名）。

用法：python scripts/probe_mcp_raw.py ["兼职1群"] ["26/11/3"]
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.mcp_wechat import _run_in_proactor_loop  # noqa: E402


async def raw_call(group: str, target: str):
    """直接调用 MCP 工具并返回原始结果（不经过解析）"""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from agent.mcp_wechat import WECHAT_MCP_FOLDER, WECHAT_MCP_PYTHON

    client = MultiServerMCPClient({
        "wechat": {
            "transport": "stdio",
            "command": str(WECHAT_MCP_PYTHON),
            "args": ["-m", "mcp_server_wechat", f"--folder-path={WECHAT_MCP_FOLDER}"],
        }
    })
    tools = await client.get_tools()
    tool = next(t for t in tools if t.name == "wechat_get_chat_history")
    return await tool.ainvoke({"to_user": group, "target_date": target})


def main() -> int:
    group = sys.argv[1] if len(sys.argv) > 1 else "兼职1群"
    target = sys.argv[2] if len(sys.argv) > 2 else "26/11/3"
    result = _run_in_proactor_loop(raw_call(group, target))
    print("type:", type(result))
    print("len(str):", len(str(result)))
    s = str(result)
    print("head:", s[:300])
    try:
        parsed = json.loads(s)
        print("json type:", type(parsed))
        if isinstance(parsed, list):
            print("list len:", len(parsed))
            for item in parsed[:3]:
                print("  item type:", type(item), "keys:", list(item.keys()) if isinstance(item, dict) else None)
                if isinstance(item, dict):
                    print("  item preview:", json.dumps({k: (str(v)[:60]) for k, v in item.items()}, ensure_ascii=False)[:300])
        elif isinstance(parsed, dict):
            print("dict keys:", list(parsed.keys()))
    except Exception as e:
        print("json.loads failed:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())