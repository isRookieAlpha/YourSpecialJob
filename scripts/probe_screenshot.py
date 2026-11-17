# -*- coding: utf-8 -*-
"""
截图通道真实验证：生成模拟微信聊天截图 → 弱模型多模态转写 → 入库 → 清理。

用法：python scripts/probe_screenshot.py [真实截图路径]
- 不传参数时用 PIL 生成的模拟截图（验证链路；识别质量以真实微信截图为准）
- 传图片路径时直接识别该文件

测试群名固定「截图识别测试」，结束后自动清理该群测试数据。
"""
import asyncio
import io
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.db.pool import get_pool  # noqa: E402
from agent.models_factory import is_model_configured  # noqa: E402
from agent.screenshot import ingest_screenshot, transcribe_image  # noqa: E402

TEST_GROUP = "截图识别测试"
TARGET = __import__("datetime").date(2026, 11, 3)


def make_fake_screenshot() -> bytes:
    """生成模拟微信聊天截图（灰底 + 白色气泡 + 中文昵称/消息）"""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(
        os.getenv("REPORT_FONT_PATH", "C:/Windows/Fonts/msyh.ttc"), 22
    )
    img = Image.new("RGB", (750, 560), (237, 237, 237))
    draw = ImageDraw.Draw(img)
    messages = [
        ("张姐  10:15", "酒店宴会传菜日结110，7:30-21:00，静安寺，加微信abc123"),
        ("李哥  10:20", "仓库搬货200一天，松江，男生优先，联系13800000000"),
        ("小美  10:25", "明天下午会展中心协助，120半天，轻松活"),
    ]
    y = 60
    for sender, text in messages:
        draw.text((40, y), sender, font=font, fill=(110, 110, 110))
        y += 34
        # 白色气泡（两行）
        lines = [text[:28], text[28:]] if len(text) > 28 else [text]
        box_h = 30 * len(lines) + 20
        draw.rounded_rectangle((40, y, 710, y + box_h), radius=14, fill=(255, 255, 255))
        for i, line in enumerate(lines):
            draw.text((56, y + 12 + i * 30), line, font=font, fill=(20, 20, 20))
        y += box_h + 36
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    if not is_model_configured("weak"):
        print("请先在 agent/.env 填写 WEAK 模型配置")
        return 2

    if len(sys.argv) > 1:
        image_bytes = Path(sys.argv[1]).read_bytes()
        print(f"使用真实截图：{sys.argv[1]}")
    else:
        image_bytes = make_fake_screenshot()
        print("使用 PIL 生成的模拟聊天截图")

    #1.先单独验证转写
    print(f"\n{'=' * 70}\n多模态转写结果：\n{'=' * 70}")
    try:
        text = await transcribe_image(image_bytes)
        print(text[:600])
    except RuntimeError as e:
        print(f"转写失败：{e}")
        return 1

    #2.走完整入库管道
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM jobs WHERE group_name = %s", (TEST_GROUP,))

    print(f"\n{'=' * 70}\n完整入库管道：\n{'=' * 70}")
    try:
        stats = await ingest_screenshot(TEST_GROUP, TARGET, image_bytes, source_ref="probe_fake_screen.png")
        print(f"统计：{stats.model_dump()}")
    except RuntimeError as e:
        print(f"入库失败：{e}")
        return 1

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT source, work_summary, commission_min, location FROM jobs WHERE group_name = %s",
            (TEST_GROUP,),
        )
        rows = await cur.fetchall()
    print(f"\n入库记录（{len(rows)} 条）：")
    for r in rows:
        print(f"  source={r[0]} summary={r[1]} commission={r[2]} location={r[3]}")

    ok = len(rows) >= 1 and all(r[0] == "manual_image" for r in rows)
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM jobs WHERE group_name = %s", (TEST_GROUP,))
    print(f"\n测试数据已清理。结果：{'通过' if ok else '失败'}（入库 {len(rows)} 条）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))