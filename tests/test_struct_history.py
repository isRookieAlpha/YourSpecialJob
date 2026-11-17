# -*- coding: utf-8 -*-
"""struct_history.py 测试：追加 / 去重读取 / 文件不存在 / 坏行容忍。"""
from agent.memory.struct_history import append_items, history_path, load_seen_hashes


def test_history_path_sanitizes_group_name(tmp_path, monkeypatch):
    """群名中的非法字符被替换，中文保留"""
    import agent.memory.struct_history as sh
    monkeypatch.setattr(sh, "HISTORY_DIR", tmp_path)
    path = history_path("兼职/1群:测试")
    assert path.name == "兼职_1群_测试_wechat_auto_struct_history.jsonl"
    assert path.parent == tmp_path


async def test_append_and_load_roundtrip(tmp_path):
    """追加两条后能读回两个 hash"""
    path = tmp_path / "g.jsonl"
    await append_items(path, [
        {"content_hash": "aaa", "raw_text": "x"},
        {"content_hash": "bbb", "raw_text": "y"},
    ])
    assert await load_seen_hashes(path) == {"aaa", "bbb"}


async def test_load_missing_file_returns_empty(tmp_path):
    """文件不存在返回空集"""
    assert await load_seen_hashes(tmp_path / "nope.jsonl") == set()


async def test_load_skips_bad_lines(tmp_path):
    """坏 JSON 行被跳过，不影响其他行"""
    path = tmp_path / "g.jsonl"
    path.write_text('{"content_hash": "ok1"}\nnot-json\n{"content_hash": "ok2"}\n', encoding="utf-8")
    assert await load_seen_hashes(path) == {"ok1", "ok2"}


async def test_append_empty_no_file_created(tmp_path):
    """空记录不创建文件"""
    path = tmp_path / "empty.jsonl"
    await append_items(path, [])
    assert not path.exists()