# -*- coding: utf-8 -*-
"""
微信 MCP 连通性验证（M0 决策门）。

用 langchain-mcp-adapters 通过 stdio 连接隔离环境 SggWechatMCP 中的 mcp_server_wechat，
拉取工具列表并打印（只读，不修改任何数据）。

前置：
- conda 环境 SggWechatMCP 已安装 mcp_server_wechat，并运行过 scripts/patch_pywechat.py
- PC 微信桌面端已登录（部分工具执行时需要，仅列工具列表一般不需要）

用法：python scripts/check_wechat_mcp.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

#1.隔离环境中的 Python 解释器（mcp_server_wechat 及其旧版依赖只装在这里）
WECHAT_MCP_PYTHON = Path(
    os.getenv("WECHAT_MCP_PYTHON")
    or (Path.home() / ".conda" / "envs" / "SggWechatMCP" / "python.exe")
)

#2.微信导出文件目录（参照 demo 项目 data/wechat_history 的用法）
WECHAT_MCP_FOLDER = ROOT / "data" / "wechat_history"


async def main() -> int:
    #3.确保导出目录存在（server 可能写入/读取该目录）
    WECHAT_MCP_FOLDER.mkdir(parents=True, exist_ok=True)

    #4.建立 stdio MCP 客户端连接
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({
        "wechat": {
            "transport": "stdio",
            "command": str(WECHAT_MCP_PYTHON),
            "args": ["-m", "mcp_server_wechat", f"--folder-path={WECHAT_MCP_FOLDER}"],
        }
    })

    #5.拉取工具列表（这一步即可验证 server 能否正常启动并完成 MCP 握手）
    tools = await client.get_tools()
    print(f"微信 MCP 连接成功，工具数量: {len(tools)}")
    for t in tools:
        desc = (t.description or "").strip().splitlines()[0][:100] if t.description else ""
        print(f"  - {t.name}: {desc}")
    return 0


if __name__ == "__main__":
    #6.异常兜底：连接失败时明确提示降级路径，不影响项目其他功能
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"微信 MCP 连接失败: {type(e).__name__}: {e}")
        print("降级路径：手动粘贴聊天记录 / 上传聊天截图仍可完整使用")
        sys.exit(1)