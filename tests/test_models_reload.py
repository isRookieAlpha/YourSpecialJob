# -*- coding: utf-8 -*-
"""models_factory 缓存与重置测试（不联网：init_chat_model / OpenAIEmbeddings 构造均不发请求）。"""
import pytest

import agent.models_factory as mf
from agent.config import Settings


@pytest.fixture
def fake_settings(monkeypatch):
    """固定配置，避免依赖本机 .env"""
    s = Settings(_env_file=None)
    s.strong_model, s.strong_api_key, s.strong_base_url = "m", "k", "u"
    s.weak_model, s.weak_api_key, s.weak_base_url = "m", "k", "u"
    s.backup_model, s.backup_api_key, s.backup_base_url = "b", "k", "u"
    s.embedding_model, s.embedding_api_key, s.embedding_base_url = "e", "k", "u"
    s.embedding_dims = 8
    monkeypatch.setattr(mf, "get_settings", lambda: s)
    mf.reset_models()
    yield s
    mf.reset_models()


def test_reset_models_clears_all_caches(fake_settings):
    """五个缓存全部清零（漏清会导致保存了配置却不生效）"""
    mf.get_llm_strong()
    mf.get_llm_weak()
    mf.get_llm_backup()
    mf.get_extractor()
    mf.get_embeddings()

    assert mf.get_llm_strong.cache_info().currsize == 1
    assert mf.get_llm_weak.cache_info().currsize == 1
    assert mf.get_llm_backup.cache_info().currsize == 1
    assert mf.get_extractor.cache_info().currsize == 1
    assert mf.get_embeddings.cache_info().currsize == 1

    mf.reset_models()

    assert mf.get_llm_strong.cache_info().currsize == 0
    assert mf.get_llm_weak.cache_info().currsize == 0
    assert mf.get_llm_backup.cache_info().currsize == 0
    assert mf.get_extractor.cache_info().currsize == 0
    assert mf.get_embeddings.cache_info().currsize == 0


def test_backup_returns_none_when_unconfigured(fake_settings):
    """备用模型未配置返回 None（graph 据此省略 fallback 中间件）"""
    fake_settings.backup_model = ""
    mf.reset_models()
    assert mf.get_llm_backup() is None