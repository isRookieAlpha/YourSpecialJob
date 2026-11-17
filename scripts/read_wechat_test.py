# -*- coding: utf-8 -*-
"""
微信 MCP 真实读取测试（只读，不改数据）。

调用 wechat_get_chat_history 读取指定群在指定日期的聊天记录。
注意：pywechat 会操作 PC 微信窗口（打开聊天历史、翻页），执行期间请勿操作微信。

用法：python scripts/read_wechat_test.py "<群名>" "<YY/M/D>"
示例：python scripts/read_wechat_test.py "兼职1群" "26/11/3"
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
WECHAT_MCP_PYTHON = Path(
    os.getenv("WECHAT_MCP_PYTHON")
    or (Path.home() / ".conda" / "envs" / "SggWechatMCP" / "python.exe")
)
WECHAT_MCP_FOLDER = ROOT / "data" / "wechat_history"


async def main() -> int:
    if len(sys.argv) < 3:
        print('用法: python scripts/read_wechat_test.py "<群名>" "<YY/M/D>"')
        return 2
    friend, target_date = sys.argv[1], sys.argv[2]

    WECHAT_MCP_FOLDER.mkdir(parents=True, exist_ok=True)

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({
        "wechat": {
            "transport": "stdio",
            "command": str(WECHAT_MCP_PYTHON),
            "args": ["-m", "mcp_server_wechat", f"--folder-path={WECHAT_MCP_FOLDER}"],
        }
    })

    #1.找到读取工具并打印其参数定义
    tools = await client.get_tools()
    tool = next((t for t in tools if t.name == "wechat_get_chat_history"), None)
    if tool is None:
        print("未找到 wechat_get_chat_history 工具")
        return 1
    print("工具参数:", json.dumps(getattr(tool, "args", {}), ensure_ascii=False))

    #2.执行真实读取（会操作微信窗口，可能耗时较久）
    print(f"开始读取：群={friend} 日期={target_date}（请勿操作微信）...")
    try:
        result = await tool.ainvoke({"to_user": friend, "target_date": target_date})
    except Exception as e:
        print(f"调用失败: {type(e).__name__}: {e}")
        return 1

    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    print(f"读取返回（共 {len(text)} 字符，显示前 3000 字符）:")
    print(text[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))