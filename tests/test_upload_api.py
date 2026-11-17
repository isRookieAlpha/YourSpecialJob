# -*- coding: utf-8 -*-
"""POST /api/upload/screenshot API 测试（mock 摄入，校验文件限制）。"""
import io

import pytest
from PIL import Image

import backend.routers.upload as upload_mod
from agent.models.job import IngestStats


def make_png(width: int = 60, height: int = 40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 210, 220)).save(buf, format="PNG")
    return buf.getvalue()


def fake_stats() -> IngestStats:
    return IngestStats(
        group_name="兼职1群", target_date="2026-11-03", source_ref="abc.img",
        total_messages=2, noise_filtered=0, batches=1, llm_valid=1, new_inserted=1,
        dup_in_run=0, dup_jsonl=0, dup_db=0, elapsed_sec=1.2,
    )


@pytest.fixture
def patch_upload(tmp_path, monkeypatch):
    """替换上传目录与摄入函数"""
    monkeypatch.setattr(upload_mod, "UPLOAD_DIR", tmp_path)

    async def fake_ingest(group_name, target_date, image_bytes, source_ref):
        return fake_stats()

    monkeypatch.setattr(upload_mod, "ingest_screenshot", fake_ingest)
    return tmp_path


async def test_upload_success(api_client, patch_upload):
    """正常上传 → 200 + IngestStats"""
    resp = await api_client.post(
        "/api/upload/screenshot",
        data={"group_name": "兼职1群", "target_date": "2026-11-03"},
        files={"file": ("shot.png", make_png(), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_inserted"] == 1
    # 文件已落盘（uuid 重命名 .img）
    assert len(list(patch_upload.glob("*.img"))) == 1


async def test_upload_bad_content_type(api_client, patch_upload):
    """非 PNG/JPEG/WebP → 400"""
    resp = await api_client.post(
        "/api/upload/screenshot",
        data={"group_name": "兼职1群", "target_date": "2026-11-03"},
        files={"file": ("shot.gif", b"GIF89a", "image/gif")},
    )
    assert resp.status_code == 400
    assert "不支持的图片类型" in resp.json()["detail"]


async def test_upload_oversize(api_client, patch_upload):
    """超过 8MB → 413"""
    big = b"x" * (8 * 1024 * 1024 + 1)
    resp = await api_client.post(
        "/api/upload/screenshot",
        data={"group_name": "兼职1群", "target_date": "2026-11-03"},
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 413


async def test_upload_fake_image(api_client, patch_upload):
    """content-type 正确但内容不是图片 → 400（pillow 校验）"""
    resp = await api_client.post(
        "/api/upload/screenshot",
        data={"group_name": "兼职1群", "target_date": "2026-11-03"},
        files={"file": ("fake.png", b"not really a png", "image/png")},
    )
    assert resp.status_code == 400
    assert "无法识别" in resp.json()["detail"]


async def test_upload_model_unsupported(api_client, patch_upload, monkeypatch):
    """弱模型不支持图片 → 502 + 中文提示"""
    async def failing_ingest(group_name, target_date, image_bytes, source_ref):
        raise RuntimeError("当前弱模型不支持图片识别，请在设置页更换支持图片输入的多模态模型")

    monkeypatch.setattr(upload_mod, "ingest_screenshot", failing_ingest)
    resp = await api_client.post(
        "/api/upload/screenshot",
        data={"group_name": "兼职1群", "target_date": "2026-11-03"},
        files={"file": ("shot.png", make_png(), "image/png")},
    )
    assert resp.status_code == 502
    assert "不支持图片识别" in resp.json()["detail"]