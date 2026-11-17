# -*- coding: utf-8 -*-
"""简历解析/切块/索引/检索测试（mock embedding，真实测试库）。"""
from types import SimpleNamespace

import pytest

import agent.rag as rag
from agent.db.resume_dao import RESUME_VECTOR_DIMS, create_file


def vec(index: int) -> list[float]:
    values = [0.0] * RESUME_VECTOR_DIMS
    values[index] = 1.0
    return values


class FakeEmbeddings:
    async def aembed_documents(self, texts):
        return [vec(i) for i in range(len(texts))]

    async def aembed_query(self, text):
        return vec(0)


class TestExtract:
    def test_txt_utf8(self):
        assert rag.extract_text("a.txt", "你好\n世界".encode("utf-8")) == "你好\n世界"

    def test_md(self):
        assert rag.extract_text("a.md", "# 标题".encode("utf-8")) == "# 标题"

    def test_gbk_fallback(self):
        assert rag.extract_text("a.txt", "中文简历".encode("gb18030")) == "中文简历"

    def test_unsupported(self):
        with pytest.raises(rag.ResumeParseError, match="不支持"):
            rag.extract_text("a.exe", b"x")

    def test_empty_text(self):
        with pytest.raises(rag.ResumeParseError, match="未能"):
            rag.extract_text("a.txt", b"   ")


class TestSplit:
    def test_chunks_size(self):
        text = "第一段内容。" * 300
        chunks = rag.split_text(text)
        assert len(chunks) > 1
        assert all(len(chunk) <= 500 for chunk in chunks)


class TestIndexAndSearch:
    async def test_index_and_search(self, pool, monkeypatch):
        monkeypatch.setattr(rag, "get_embeddings", lambda: FakeEmbeddings())
        file_id = await create_file(
            pool, filename="cv.txt", stored_path="x", mime="text/plain",
            size_bytes=10, sha256="h",
        )
        count = await rag.index_resume(pool, file_id, "自我介绍：擅长活动执行。" * 60)
        assert count >= 1

        hits = await rag.search_resume(pool, "自我介绍")
        assert hits
        assert hits[0]["filename"] == "cv.txt"
        assert hits[0]["score"] > 0

    async def test_dims_mismatch(self, monkeypatch):
        monkeypatch.setattr(
            rag, "get_settings", lambda: SimpleNamespace(embedding_dims=768)
        )
        with pytest.raises(rag.ResumeParseError, match="1024"):
            await rag.embed_texts(["x"])

    async def test_empty_query(self, pool):
        assert await rag.search_resume(pool, "  ") == []