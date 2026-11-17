# -*- coding: utf-8 -*-
"""截图通道测试：图片预处理 / 多模态转写（mock）/ 入库（mock extractor，真实测试库）。"""
import io
from datetime import date

import pytest
from PIL import Image

import agent.ingest as ingest_mod
import agent.screenshot as shot_mod
from agent.models.job import ExtractedBatch, JobExtractItem, JobMessage
from agent.screenshot import MULTIMODAL_UNSUPPORTED_MSG, ingest_screenshot, prepare_image


def make_png(width: int = 60, height: int = 40) -> bytes:
    """生成测试 PNG 字节"""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 210, 220)).save(buf, format="PNG")
    return buf.getvalue()


class TestPrepareImage:
    def test_valid_png_to_jpeg(self):
        data, mime = prepare_image(make_png())
        assert mime == "image/jpeg"
        img = Image.open(io.BytesIO(data))
        assert img.format == "JPEG"

    def test_invalid_bytes_raises(self):
        with pytest.raises(ValueError, match="无法识别"):
            prepare_image(b"not an image")

    def test_large_image_downscaled(self):
        data, _ = prepare_image(make_png(3000, 1000))
        img = Image.open(io.BytesIO(data))
        assert max(img.size) == shot_mod.MAX_EDGE_PX


class FakeLLM:
    """模拟弱模型多模态调用"""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error

    async def ainvoke(self, messages):
        if self.error:
            raise self.error

        class Resp:
            content = self.content
        return Resp()


class TestTranscribeImage:
    async def test_success_returns_text(self, monkeypatch):
        monkeypatch.setattr(shot_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(shot_mod, "get_llm_weak", lambda: FakeLLM("张姐 10:15\n酒店传菜日结110"))
        text = await shot_mod.transcribe_image(make_png())
        assert "酒店传菜日结110" in text

    async def test_model_error_gives_chinese_hint(self, monkeypatch):
        """多模态调用失败 → 中文错误提示（不崩溃、无 OCR 分支）"""
        monkeypatch.setattr(shot_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(shot_mod, "get_llm_weak", lambda: FakeLLM(error=RuntimeError("model does not support image")))
        with pytest.raises(RuntimeError, match="不支持图片识别"):
            await shot_mod.transcribe_image(make_png())

    async def test_no_content_raises(self, monkeypatch):
        monkeypatch.setattr(shot_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(shot_mod, "get_llm_weak", lambda: FakeLLM("无聊天内容"))
        with pytest.raises(RuntimeError, match="未识别到聊天消息"):
            await shot_mod.transcribe_image(make_png())


TARGET = date(2026, 11, 3)
GROUP = "兼职1群"


def _batch(text: str) -> ExtractedBatch:
    return ExtractedBatch(items=[
        JobExtractItem(
            source_index=0,
            raw_text=text,
            is_valid_job=True,
            job=JobMessage(work_summary="传菜", commission_min=110, commission_max=110,
                           commission_unit="元/天"),
        )
    ])


class FakeExtractor:
    def __init__(self, answer: ExtractedBatch):
        self.answer = answer

    async def ainvoke(self, prompt: str) -> ExtractedBatch:
        return self.answer


async def test_ingest_screenshot_pipeline(monkeypatch, pool, tmp_path):
    """截图转写文本 → 入库，source=manual_image，source_ref=文件名"""
    async def fake_transcribe(image_bytes: bytes) -> str:
        return "张姐 10:15\n酒店传菜日结110，加微信abc"

    async def fake_pool():
        return pool

    monkeypatch.setattr(shot_mod, "transcribe_image", fake_transcribe)
    monkeypatch.setattr(ingest_mod, "get_extractor", lambda: FakeExtractor(_batch("酒店传菜日结110，加微信abc")))
    monkeypatch.setattr(ingest_mod, "get_pool", fake_pool)
    monkeypatch.setattr(
        ingest_mod, "history_path", lambda group, source="wechat_auto": tmp_path / f"{source}.jsonl"
    )

    stats = await ingest_screenshot(GROUP, TARGET, b"fake-image", source_ref="abc123.img")
    assert stats.new_inserted == 1
    assert stats.source_ref == "abc123.img"

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT source, source_ref FROM jobs WHERE group_name = %s", (GROUP,))
        rows = await cur.fetchall()
    assert rows == [("manual_image", "abc123.img")]

    # jsonl 按 source 分文件
    assert (tmp_path / "manual_image.jsonl").exists()