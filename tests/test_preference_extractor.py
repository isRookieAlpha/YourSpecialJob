# -*- coding: utf-8 -*-
"""长期记忆提取测试（mock 弱模型提取，fake store 记录写入）。"""
from agent.models.memory import MemoryBatch, MemoryItem
from agent.memory.preference_extractor import extract_and_store_memory
import agent.memory.preference_extractor as extract_mod


class FakeStore:
    index_config = None

    def __init__(self, similar: str | None = None):
        self.puts: list[tuple[str, dict]] = []
        self.similar = similar

    async def asearch(self, namespace, query=None, limit=1, **kwargs):
        class Hit:
            key = "existing-key"
            score = 0.95
            value = {"text": "旧记忆"}
        return [Hit()] if self.similar else []

    async def aput(self, namespace, key, value, index=None, **kwargs):
        self.puts.append((key, dict(value)))


class FakeExtractor:
    def __init__(self, items):
        self.items = items

    async def ainvoke(self, prompt):
        return MemoryBatch(items=self.items)


def patch_extractor(monkeypatch, items):
    monkeypatch.setattr(extract_mod, "is_model_configured", lambda kind: True)
    monkeypatch.setattr(extract_mod, "get_memory_extractor", lambda: FakeExtractor(items))


class TestExtractAndStore:
    async def test_basic_store(self, monkeypatch):
        patch_extractor(monkeypatch, [
            MemoryItem(kind="preference", text="日结优先", confidence=0.9),
            MemoryItem(kind="constraint", text="不接夜班", confidence=0.85),
        ])
        store = FakeStore()
        saved = await extract_and_store_memory(store, "t1", "我找兼职，日结优先，不接夜班", "好的")
        assert saved == 2
        assert len(store.puts) == 2
        assert {v["kind"] for _, v in store.puts} == {"preference", "constraint"}
        assert all(v["source_thread"] == "t1" for _, v in store.puts)

    async def test_no_store(self):
        assert await extract_and_store_memory(None, "t1", "找兼职", "") == 0

    async def test_weak_model_missing(self, monkeypatch):
        monkeypatch.setattr(extract_mod, "is_model_configured", lambda kind: False)
        store = FakeStore()
        assert await extract_and_store_memory(store, "t1", "找兼职", "") == 0
        assert store.puts == []

    async def test_short_message_skipped(self, monkeypatch):
        patch_extractor(monkeypatch, [MemoryItem(kind="fact", text="x")])
        store = FakeStore()
        assert await extract_and_store_memory(store, "t1", "嗯", "") == 0
        assert store.puts == []

    async def test_extractor_failure_degrades(self, monkeypatch):
        class Failing:
            async def ainvoke(self, prompt):
                raise RuntimeError("model down")

        monkeypatch.setattr(extract_mod, "is_model_configured", lambda kind: True)
        monkeypatch.setattr(extract_mod, "get_memory_extractor", lambda: Failing())
        store = FakeStore()
        assert await extract_and_store_memory(store, "t1", "找兼职", "") == 0
        assert store.puts == []

    async def test_dedupe_with_index(self, monkeypatch):
        """有向量索引且相似度高 → 复用已有 key（合并而非新增）"""
        patch_extractor(monkeypatch, [MemoryItem(kind="preference", text="日结优先")])
        store = FakeStore(similar="旧记忆")
        store.index_config = {"dims": 1024}
        saved = await extract_and_store_memory(store, "t1", "我要日结的工作", "")
        assert saved == 1
        assert store.puts[0][0] == "existing-key"