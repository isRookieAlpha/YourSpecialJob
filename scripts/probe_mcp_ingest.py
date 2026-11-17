# -*- coding: utf-8 -*-
"""
真实摄入管道验证：微信读取 → 结构化 → 入库（含幂等复跑）。

用法：python scripts/probe_mcp_ingest.py ["兼职1群"] ["26/11/3"]
注意：执行期间不要操作微信（pywechat 会实际操纵微信窗口）。
幂等验证：连续跑两遍，第二遍 new_inserted 应为 0。
"""
import asyncio
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# psycopg 3.x 不支持 Windows ProactorEventLoop：主循环用 Selector（MCP 在独立线程用 Proactor）
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.ingest import ingest_group  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402


async def main() -> int:
    if not is_model_configured("weak"):
        print("请先在 agent/.env 填写 WEAK 模型配置")
        return 2

    group = sys.argv[1] if len(sys.argv) > 1 else "兼职1群"
    target = sys.argv[2] if len(sys.argv) > 2 else "26/11/3"

    for run_no in (1, 2):
        print(f"\n{'=' * 60}\n第 {run_no} 遍摄入（幂等验证）\n{'=' * 60}")
        try:
            stats = await ingest_group(group, target)
        except Exception as e:
            print(f"摄入失败：{type(e).__name__}: {e}")
            return 1
        print(json.dumps(stats.model_dump(), ensure_ascii=False, indent=2))
        if run_no == 1 and stats.new_inserted == 0:
            print("注意：第一遍就没有新入库，检查是否此前已摄入过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))