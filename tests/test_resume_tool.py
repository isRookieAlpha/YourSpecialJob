# -*- coding: utf-8 -*-
"""search_resume 工具测试（mock 检索，不调 embedding）。"""
import agent.tools.resume as tool_mod
from agent.tools.resume import search_resume


async def _fake_pool():
    return object()


class TestSearchResumeTool:
    async def test_hits(self, monkeypatch):
        async def fake_search(pool, query, top_k):
            return [{
                "filename": "cv.pdf",
                "chunk_index": 0,
                "page": 1,
                "text": "擅长活动执行与现场协调",
                "score": 0.83,
            }]

        monkeypatch.setattr(tool_mod, "get_pool", _fake_pool)
        monkeypatch.setattr(tool_mod, "rag_search", fake_search)

        text = await search_resume.ainvoke({"query": "活动执行经历"})
        assert "cv.pdf" in text
        assert "擅长活动执行" in text
        assert "0.83" in text

    async def test_empty_library(self, monkeypatch):
        async def fake_search(pool, query, top_k):
            return []

        monkeypatch.setattr(tool_mod, "get_pool", _fake_pool)
        monkeypatch.setattr(tool_mod, "rag_search", fake_search)

        text = await search_resume.ainvoke({"query": "自我介绍"})
        assert "简历库为空或未命中" in text
        assert "不要编造" in text

    async def test_empty_query(self):
        text = await search_resume.ainvoke({"query": "   "})
        assert "检索词为空" in text

    async def test_search_failure(self, monkeypatch):
        async def failing(pool, query, top_k):
            raise RuntimeError("embedding down")

        monkeypatch.setattr(tool_mod, "get_pool", _fake_pool)
        monkeypatch.setattr(tool_mod, "rag_search", failing)

        text = await search_resume.ainvoke({"query": "经历"})
        assert "简历检索失败" in text