# -*- coding: utf-8 -*-
"""简历上传/列表/删除/重建 API 测试（tmp 目录 + mock 索引）。"""
import backend.routers.resume as resume_mod
from agent import rag
from agent.db import resume_dao


async def fake_index(pool, file_id, text):
    await resume_dao.update_file_status(pool, file_id, status="ready", chunk_count=3)
    return 3


class TestUpload:
    async def test_upload_txt(self, api_client, pool, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(resume_mod.rag, "index_resume", fake_index)

        resp = await api_client.post(
            "/api/resume/upload",
            files={"file": ("cv.txt", "自我介绍：擅长活动执行".encode("utf-8"), "text/plain")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "cv.txt"
        assert data["chunk_count"] == 3

        files = await resume_dao.list_files(pool)
        assert len(files) == 1
        assert files[0]["chunk_count"] == 3
        assert len(list(tmp_path.iterdir())) == 1

    async def test_reject_unsupported_type(self, api_client, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)
        resp = await api_client.post(
            "/api/resume/upload",
            files={"file": ("cv.exe", b"x", "application/octet-stream")},
        )
        assert resp.status_code == 422
        assert "PDF/DOCX" in resp.json()["detail"]

    async def test_reject_too_large(self, api_client, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)
        big = b"a" * (resume_mod.MAX_SIZE_BYTES + 1)
        resp = await api_client.post(
            "/api/resume/upload",
            files={"file": ("cv.txt", big, "text/plain")},
        )
        assert resp.status_code == 413

    async def test_index_failure_cleans_up(self, api_client, pool, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)

        async def failing_index(pool_, file_id, text):
            raise RuntimeError("embedding 不可用")

        monkeypatch.setattr(resume_mod.rag, "index_resume", failing_index)
        resp = await api_client.post(
            "/api/resume/upload",
            files={"file": ("cv.txt", "内容".encode("utf-8"), "text/plain")},
        )
        assert resp.status_code == 500
        assert await resume_dao.list_files(pool) == []
        assert list(tmp_path.iterdir()) == []


class TestListAndDelete:
    async def test_delete_removes_file_and_record(
        self, api_client, pool, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(resume_mod.rag, "index_resume", fake_index)
        resp = await api_client.post(
            "/api/resume/upload",
            files={"file": ("cv.txt", "内容".encode("utf-8"), "text/plain")},
        )
        file_id = resp.json()["id"]

        listed = await api_client.get("/api/resume/files")
        assert listed.json()["files"][0]["id"] == file_id

        deleted = await api_client.delete(f"/api/resume/files/{file_id}")
        assert deleted.status_code == 200
        assert list(tmp_path.iterdir()) == []
        assert await resume_dao.list_files(pool) == []

    async def test_delete_missing_404(self, api_client):
        resp = await api_client.delete("/api/resume/files/9999")
        assert resp.status_code == 404


class TestRebuild:
    async def test_rebuild(self, api_client, pool, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "RESUME_UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(resume_mod.rag, "index_resume", fake_index)
        await api_client.post(
            "/api/resume/upload",
            files={"file": ("a.txt", "内容 A".encode("utf-8"), "text/plain")},
        )
        await api_client.post(
            "/api/resume/upload",
            files={"file": ("b.md", "# 内容 B".encode("utf-8"), "text/markdown")},
        )

        resp = await api_client.post("/api/resume/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert data["files"] == 2
        assert data["chunks"] == 6
        assert data["failed"] == []