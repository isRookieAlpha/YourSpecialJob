# -*- coding: utf-8 -*-
"""patch_pywechat 测试：补丁替换 / 幂等 / verify 校验（模拟目标文件，不碰真实环境）。"""
from pathlib import Path

import pytest

import scripts.patch_pywechat as patch_mod

FAKE_CLIENT = '''\
def get_chat_history_by_date(self, friend: str, target_date: str, folder_path: str = None,
                             search_pages: int = 5, is_maximize: bool = False,
                             close_wechat: bool = True, scroll_delay: float = 0.01):
    if True:  # 模拟真实查找循环的缩进层级（补丁 #26 按精确缩进匹配）
        while not found_target_date:
            for record in info:
                _, time_str, _ = record
                msg_date = parse_date(time_str)
                if msg_date:
                    msg_date_obj = msg_date.date() if hasattr(msg_date, 'date') else msg_date
                    target_date_obj = target_datetime.date() if hasattr(target_datetime, 'date') else target_datetime

                    if msg_date_obj == target_date_obj:
                        found_target_date = True
                        break
                    elif msg_date_obj < target_date_obj:
                        found_earlier_date = True
                        if not found_target_date:
                            break


def send_message_to_friend(self, friend: str, message: str,
                           search_pages: int = 0, delay: float = 1.0):
    try:
        Messages.send_messages_to_friend(
                friend=friend,
                messages=[message],
                delay=delay,
                search_pages=search_pages
            )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_messages_to_friend(self, friend: str, messages):
    try:
        Messages.send_messages_to_friend(
                friend=friend,
                messages=messages,
                delay=delay,
                search_pages=search_pages
            )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_message_to_friends(self, friends, message, delay: float = 1.0):
    try:
        Messages.send_message_to_friends(
                friends=friends,
                message=message
            )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
'''


@pytest.fixture
def fake_client(tmp_path, monkeypatch):
    path = tmp_path / "WechatClient.py"
    path.write_text(FAKE_CLIENT, encoding="utf-8")
    monkeypatch.setattr(patch_mod, "MCP_CLIENT_FILE", path)
    return path


def apply_m7_patches() -> None:
    for old, new in patch_mod.MCP_CLIENT_PATCHES:
        patch_mod.patch_file(patch_mod.MCP_CLIENT_FILE, old, new, "test")


class TestPatchApplication:
    def test_m7_patches_apply_and_verify(self, fake_client):
        apply_m7_patches()
        text = fake_client.read_text(encoding="utf-8")
        # 读取后关闭微信主窗口（用户要求操作完自动关闭）
        assert "close_wechat: bool = True, scroll_delay" in text
        # 发送参数名与关闭窗口（三处发送均为 True）
        assert text.count("send_delay=delay") >= 2
        assert text.count("close_wechat=True") >= 3
        # 多好友函数名与消息形状
        assert "Messages.send_messages_to_friends(" in text
        assert "isinstance(message, str)" in text
        # verify 通过
        assert patch_mod.verify_patches() == []

    def test_idempotent(self, fake_client):
        apply_m7_patches()
        first = fake_client.read_text(encoding="utf-8")
        applied = patch_mod.patch_file(
            fake_client, patch_mod.MCP_CLIENT_PATCHES[-1][0],
            patch_mod.MCP_CLIENT_PATCHES[-1][1], "again",
        )
        assert applied is False  # 第二次替换同一处应跳过
        assert fake_client.read_text(encoding="utf-8") == first

    def test_verify_detects_missing_patch(self, fake_client):
        problems = patch_mod.verify_patches()
        assert any("send_delay" in p for p in problems)
        assert any("close_wechat" in p for p in problems)


def test_fake_client_has_no_false_patch_left(fake_client):
    """补丁应用后不应残留 close_wechat=False（发送/读取都会关闭窗口）"""
    apply_m7_patches()
    text = fake_client.read_text(encoding="utf-8")
    assert "close_wechat=False" not in text

    def test_verify_detects_syntax_error(self, fake_client):
        fake_client.write_text("def broken(:\n", encoding="utf-8")
        problems = patch_mod.verify_patches()
        assert any("语法错误" in p for p in problems)