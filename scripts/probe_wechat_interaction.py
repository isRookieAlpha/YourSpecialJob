# -*- coding: utf-8 -*-
"""
微信互动可行性探针：验证"与自己的聊天"会话的读取与方向语义。

流程：
1. 读取 WECHAT_SENDER_ID（或参数指定）会话当天记录，打印现有消息（发送者/时间/摘要）
2. 从 PC 发送一条唯一标记，等待 8 秒后再次读取，观察该标记消息的"发送者"值
3. 输出结论：方向识别依赖内容前缀（【YourSpecialJob）是否可行

用法：python scripts/probe_wechat_interaction.py [会话名]
注意：会操作微信窗口（打开聊天记录/发送），执行期间不要操作微信。
"""
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import get_settings  # noqa: E402
from agent.mcp_wechat import (  # noqa: E402
    WechatMcpError,
    call_wechat_get_history,
    send_wechat_message,
    to_mcp_date,
)
from agent.wechat_window import wechat_window_lock  # noqa: E402

ROBOT_PREFIX = "【YourSpecialJob"


def dump(messages: list[dict], limit: int = 12) -> None:
    print(f"共 {len(messages)} 条：")
    for m in messages[-limit:]:
        text = str(m.get("消息", "")).replace("\n", " ")
        print(f"  [{m.get('时间')}] 发送者={m.get('发送者')!r} | {text[:60]}")


async def main() -> int:
    conversation = (sys.argv[1] if len(sys.argv) > 1 else get_settings().wechat_sender_id).strip()
    if not conversation:
        print("未配置 WECHAT_SENDER_ID，且未指定会话名")
        return 2

    today = to_mcp_date(date.today())
    print(f"目标会话：{conversation} 日期：{today}")

    #1.第一次读取
    try:
        async with wechat_window_lock:
            before = await call_wechat_get_history(conversation, today, allow_empty=True, timeout=180)
    except WechatMcpError as e:
        print(f"[失败] 读取会话失败：{e}")
        return 3
    print("\n===== 读取结果（发送前）=====")
    dump(before)
    before_texts = {str(m.get("消息", "")).strip() for m in before}

    #2.发送唯一标记
    marker = f"[probe-in {datetime.now().strftime('%m-%d %H:%M:%S')}] 互动读取验证"
    print(f"\n发送标记：{marker}")
    try:
        async with wechat_window_lock:
            await send_wechat_message(conversation, marker)
    except WechatMcpError as e:
        print(f"[失败] 发送失败：{e}")
        return 4
    print("[通过] 发送成功")

    #3.等待微信 UI 稳定后再次读取
    await asyncio.sleep(8)
    try:
        async with wechat_window_lock:
            after = await call_wechat_get_history(conversation, today, allow_empty=True, timeout=180)
    except WechatMcpError as e:
        print(f"[警告] 回读失败（发送已成功）：{e}")
        return 0

    print("\n===== 读取结果（发送后）=====")
    new_items = [m for m in after if str(m.get("消息", "")).strip() not in before_texts]
    dump(new_items or after)

    #4.分析方向语义
    print("\n===== 结论 =====")
    if not new_items:
        print("回读未看到新消息（微信 UI 可能未刷新）——方向识别仍按内容前缀策略")
        return 0
    for m in new_items:
        text = str(m.get("消息", "")).strip()
        print(f"新消息发送者={m.get('发送者')!r} 内容={text[:50]!r}")
        if text.startswith(ROBOT_PREFIX):
            print("  → 该消息带机器人前缀（预期不应出现，除非测试标记被改动）")
    senders = {m.get("发送者") for m in new_items}
    print(f"机器人发出的标记消息发送者集合：{senders}")
    print("建议：方向识别使用【内容前缀【YourSpecialJob】+ 入站指纹】双重策略，不以发送者为主判据。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))