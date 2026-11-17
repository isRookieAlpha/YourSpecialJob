# -*- coding: utf-8 -*-
"""长期记忆提示词测试（prompt 组装 + 记忆收集，不调 LLM/向量）。"""
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from agent.memory.memory_prompt import collect_memories, trim_memories
from agent.prompts.system import build_system_prompt


class FakeStore:
    def __init__(self, profile: str | None = None, hits: list[str] | None = None, indexed: bool = True):
        self.profile = profile
        self.hits = hits or []
        self.index_config = {"dims": 1024} if indexed else None
        self.search_calls = 0

    async def aget(self, namespace, key, **kwargs):
        if self.profile is None:
            return None
        return SimpleNamespace(value={"text": self.profile}, key=key)

    async def asearch(self, namespace, query=None, limit=10, **kwargs):
        self.search_calls += 1
        return [SimpleNamespace(value={"text": t}, key=f"k{i}", score=0.9) for i, t in enumerate(self.hits)]


class TestBuildSystemPrompt:
    def test_explicit_prefs_and_memories(self):
        prefs = SimpleNamespace(
            commission_min=120.0, max_commute_min=30, prefer_light=True,
            commute_origin="静安寺", note="日结优先",
        )
        text = build_system_prompt(prefs=prefs, memories=["用户不接夜班"])
        assert "用户显式偏好（设置页，最高优先级）" in text
        assert "最低佣金：120 元" in text
        assert "通勤起点：静安寺" in text
        assert "日结优先" in text
        assert "长期记忆（可能过时" in text
        assert "用户不接夜班" in text
        assert "显式偏好 > 长期记忆 > 通用常识" in text

    def test_default_prefs_without_settings(self):
        text = build_system_prompt()
        assert "骑车半小时内可达" in text
        assert "长期记忆（可能过时" not in text

    def test_no_memories_block(self):
        prefs = SimpleNamespace(
            commission_min=100.0, max_commute_min=30, prefer_light=True,
            commute_origin="", note="",
        )
        text = build_system_prompt(prefs=prefs, memories=[])
        assert "长期记忆（可能过时" not in text


class TestCollectMemories:
    async def test_profile_and_search(self):
        store = FakeStore(profile="用户偏好日结", hits=["不接夜班"])
        memories = await collect_memories(store, [HumanMessage(content="找兼职")])
        assert "用户偏好日结" in memories
        assert "不接夜班" in memories
        assert store.search_calls == 1

    async def test_no_store(self):
        assert await collect_memories(None, [HumanMessage(content="x")]) == []

    async def test_no_index_skips_search(self):
        store = FakeStore(profile="p", hits=["h"], indexed=False)
        memories = await collect_memories(store, [HumanMessage(content="x")])
        assert memories == ["p"]
        assert store.search_calls == 0

    async def test_search_only_on_human_message(self):
        store = FakeStore(hits=["h"])
        memories = await collect_memories(store, [SimpleNamespace(type="ai")])
        assert memories == []
        assert store.search_calls == 0


class TestTrimMemories:
    def test_truncates_by_length(self):
        from agent.memory.memory_prompt import MAX_MEMORY_CHARS
        items = ["x" * 800, "y" * 800, "z" * 800]
        trimmed = trim_memories(items)
        assert sum(len(t) for t in trimmed) <= MAX_MEMORY_CHARS
        assert trimmed[0].startswith("x")