# -*- coding: utf-8 -*-
"""
真实 LLM 结构化提取验证（读已导出的聊天记录 json，不操作微信）。

流程：读导出 json → 规则噪音过滤 → 原文去重 → 取前 N 条 → 弱模型
with_structured_output(ExtractedBatch) → 打印提取质量。

用法：python scripts/probe_extract.py [json路径] [--limit N]
默认使用 data/wechat_history 下最新的导出文件，limit=10。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.models_factory import get_extractor, is_model_configured  # noqa: E402
from agent.models.job import normalize_text  # noqa: E402
from agent.prompts.extract import build_batch_prompt  # noqa: E402

NOISE_EXACT = {"动画表情", "图片", "视频", "文件", "语音", "系统消息", "链接"}


def prefilter(messages: list[dict]) -> list[dict]:
    """规则噪音过滤 + 原文去重（保留首次出现）"""
    seen: set[str] = set()
    result = []
    for msg in messages:
        text = str(msg.get("消息", "")).strip()
        if not text or text in NOISE_EXACT:
            continue
        key = normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(msg)
    return result


async def main() -> int:
    #1.检查模型配置（key 未填时退出）
    if not is_model_configured("weak"):
        print("请在 agent/.env 填写 WEAK_MODEL / WEAK_API_KEY / WEAK_BASE_URL")
        return 2

    #2.定位导出文件
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = 10
    for a in sys.argv[1:]:
        if a.startswith("--limit"):
            limit = int(a.split("=")[1]) if "=" in a else 10
    if args:
        json_path = Path(args[0])
    else:
        files = sorted((ROOT / "data" / "wechat_history").glob("*.json"))
        if not files:
            print("data/wechat_history 下没有导出文件，请先运行 scripts/read_wechat_test.py")
            return 1
        json_path = files[0]

    messages = json.loads(json_path.read_text(encoding="utf-8"))
    print(f"文件: {json_path.name} | 原始消息: {len(messages)} 条")

    #3.预过滤 + 去重 + 截取
    unique_msgs = prefilter(messages)
    print(f"预过滤+去重后: {len(unique_msgs)} 条 | 取前 {limit} 条送入模型")
    batch = unique_msgs[:limit]

    #4.构造提示词并调用真实弱模型
    prompt = build_batch_prompt(
        group_name="兼职1群",
        target_date="26/11/3",
        target_date_cn="2026-11-03",
        work_date="2026-11-03",
        batch_idx=1,
        total_batches=1,
        messages=batch,
    )
    print("-" * 60)
    print(f"提示词字符数: {len(prompt)} | 模型: qwen3.5-omni-plus")
    print("-" * 60)

    t0 = time.time()
    extractor = get_extractor()
    result = await extractor.ainvoke(prompt)
    elapsed = time.time() - t0

    #5.打印提取结果
    print(f"耗时: {elapsed:.1f}s | items: {len(result.items)}")
    valid = [i for i in result.items if i.is_valid_job and i.job]
    print(f"有效兼职: {len(valid)} 条")
    for i, item in enumerate(result.items, 1):
        status = "有效" if item.is_valid_job else "噪音"
        print(f"\n--- [{i}] {status} | source_index={item.source_index} | raw_text[:40]={item.raw_text[:40]!r}")
        if item.job:
            print(f"    摘要={item.job.work_summary!r} 体力={item.job.physical_intensity!r} "
                  f"日期={item.job.work_date} 时段={item.job.work_time!r}")
            print(f"    佣金={item.job.commission_min}~{item.job.commission_max} {item.job.commission_unit!r} "
                  f"地点={item.job.location!r} 联系={item.job.contact!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))