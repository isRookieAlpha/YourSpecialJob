# -*- coding: utf-8 -*-
"""
对隔离环境 SggWechatMCP 中的微信 MCP 依赖打补丁（幂等，已打过则跳过）。

参照 demo 项目（C:\\AlphasHome\\AlphasCode\\LLM\\Code\\demo\\.venv-wechat）验证过的补丁集：

pywechat：
1. __init__.py：注释掉"64 位系统禁止导入"的硬检查
2. WeChatTools.py：进程名检测同时支持 Wechat.exe（微信 3.9）与 Weixin.exe（微信 4.x）

mcp_server_wechat（适配 pywechat 1.9.8 的 API 变更）：
3. WechatClient.py：
   - Systemsettings 已移除 → 改用 os.path.isdir
   - open_chat_history 从 Tools 移到 Navigator，且不再接收 wechat_path 参数
   - 删除 get_chat_history_by_date 的 wechat_path 参数

说明：修改的是第三方包源码，仅存在于隔离环境，不影响主项目环境；
重建隔离环境后需重新运行本脚本。

用法：python scripts/patch_pywechat.py
"""
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#1.隔离环境的包目录
SITE_PACKAGES = Path(
    os.getenv("WECHAT_MCP_SITE_PACKAGES")
    or (Path.home() / ".conda" / "envs" / "SggWechatMCP" / "Lib" / "site-packages")
)
PYWECHAT_DIR = SITE_PACKAGES / "pywechat"
MCP_CLIENT_FILE = SITE_PACKAGES / "mcp_server_wechat" / "WechatClient.py"

#2.pywechat 补丁一：注释掉 64 位系统检查
INIT_FILE = PYWECHAT_DIR / "__init__.py"
INIT_OLD = (
    "if sys.maxsize==2**63-1:#64位操作系统\n"
    "    raise ImportError(f'3.9版本微信已无法在64位Windows系统上使用,使用32位Window系统才可以导入使用pywechat!')"
)
INIT_NEW = (
    "# 已补丁：绕过64位系统检查（微信3.9/4.x 均可在64位 Python 下尝试导入，参照 demo 项目验证）\n"
    "# if sys.maxsize==2**63-1:#64位操作系统\n"
    "#     raise ImportError(f'3.9版本微信已无法在64位Windows系统上使用,使用32位Window系统才可以导入使用pywechat!')"
)

#3.pywechat 补丁二：进程名检测支持微信 4.x（Weixin.exe）
TOOLS_FILE = PYWECHAT_DIR / "WeChatTools.py"
TOOLS_PATCHES = [
    (
        "if process.Name.lower()=='Wechat.exe'.lower():",
        "if process.Name.lower() in ('Wechat.exe'.lower(), 'Weixin.exe'.lower()):",
    ),
    (
        "if process.Name.lower() == 'WeChat.exe'.lower():",
        "if process.Name.lower() in ('WeChat.exe'.lower(), 'Weixin.exe'.lower()):",
    ),
]

#4.mcp_server_wechat 补丁：适配 pywechat 1.9.8 API
MCP_CLIENT_PATCHES = [
    (
        "from pywechat import Systemsettings, NotFolderError, Tools, NoChatHistoryError\n"
        "from pywechat.WechatAuto import Messages",
        "from pywechat import Tools, Navigator\n"
        "from pywechat.Errors import NotFolderError, NoChatHistoryError\n"
        "from pywechat.WeChatAuto import Messages",
    ),
    # 修正已应用旧补丁中的模块大小写（pywechat.WechatAuto → pywechat.WeChatAuto）
    (
        "from pywechat.WechatAuto import Messages",
        "from pywechat.WeChatAuto import Messages",
    ),
    (
        "search_pages: int = 5, wechat_path: str = None, is_maximize: bool = False,",
        "search_pages: int = 5, is_maximize: bool = False,",
    ),
    (
        "        - wechat_path: 微信可执行文件路径\n",
        "",
    ),
    (
        "if not Systemsettings.is_dirctory(folder_path):",
        "if not os.path.isdir(folder_path):",
    ),
    (
        "chat_history_window = Tools.open_chat_history(friend=friend, wechat_path=wechat_path, is_maximize=is_maximize,\n"
        "                                                      close_wechat=close_wechat, search_pages=search_pages)[0]",
        "chat_history_window = Navigator.open_chat_history(friend=friend, is_maximize=is_maximize,\n"
        "                                                          close_wechat=close_wechat, search_pages=search_pages)[0]",
    ),
    # 补丁：强制激活聊天记录窗口——pyautogui 的全局按键只作用于前台窗口，
    # 主窗口被 close_wechat 关闭后焦点可能丢失，导致 pageup 不生效（实测：需手动滚轮才继续）
    (
        "chat_history_window = Navigator.open_chat_history(friend=friend, is_maximize=is_maximize,\n"
        "                                                          close_wechat=close_wechat, search_pages=search_pages)[0]",
        "chat_history_window = Navigator.open_chat_history(friend=friend, is_maximize=is_maximize,\n"
        "                                                          close_wechat=close_wechat, search_pages=search_pages)[0]\n"
        "        # 补丁：强制激活聊天记录窗口，确保后续 pyautogui 翻页按键落在正确窗口\n"
        "        try:\n"
        "            chat_history_window.set_focus()\n"
        "            time.sleep(0.3)\n"
        "        except Exception:\n"
        "            pass",
    ),
    # 补丁：keyDown 不发送 keyUp 且模拟键盘不会触发系统自动重复 → 改用 press（完整按下+抬起）
    # 文件中有两处（定位目标日期循环一次、收集消息循环一次），故写两条
    (
        "            pyautogui.keyDown('pageup', _pause=False)",
        "            pyautogui.press('pageup')",
    ),
    (
        "            pyautogui.keyDown('pageup', _pause=False)",
        "            pyautogui.press('pageup')",
    ),
    # 补丁：键盘 PageUp 依赖前台焦点（实测常失效，需手动滚轮）；改用鼠标滚轮滚动
    # 聊天记录窗口中心——与手动滚轮同一通道，不依赖键盘焦点。两处同样替换
    (
        "            pyautogui.press('pageup')",
        "            _rec = chat_history_window.rectangle()\n"
        "            mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
    ),
    (
        "            pyautogui.press('pageup')",
        "            _rec = chat_history_window.rectangle()\n"
        "            mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
    ),
    # 补丁：首轮滚轮加长渲染等待（原 scroll_delay=0.01 太短，UI 未渲染会误判提前退出）
    (
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=5)",
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
    ),
    (
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=5)",
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
    ),
    # 调参实验结论（M1 实测）：3 格/0.3s → 10 格/0.1s，滚动耗时 ~19s → ~4s（总读取 29s → 14.3s），
    # 唯一非噪音消息数保持 21 条（无遗漏）。再加大（15 格/0.05s）只再快 0.6s，瓶颈是窗口打开固定开销。
    # 两处滚动同样替换
    (
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=10)\n"
        "            time.sleep(0.1)",
    ),
    (
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=3)\n"
        "            time.sleep(0.3)",
        "mouse.scroll(coords=((_rec.left + _rec.right) // 2, (_rec.top + _rec.bottom) // 2), wheel_dist=10)\n"
        "            time.sleep(0.1)",
    ),
    # ===== M7：发送消息可用性补丁 =====
    # 读取聊天记录后不再关闭微信主窗口（后台跟踪轮询频繁，反复开关窗口会打扰用户）
    (
        "close_wechat: bool = True, scroll_delay: float = 0.01):",
        "close_wechat: bool = False, scroll_delay: float = 0.01):",
    ),
    # 单好友发送：pywechat 1.9.8 用 send_delay 而非 delay；显式 close_wechat=False
    (
        "messages=[message],\n                delay=delay,\n                search_pages=search_pages",
        "messages=[message],\n                send_delay=delay,\n                close_wechat=False,\n"
        "                search_pages=search_pages",
    ),
    # 单好友多消息发送：同样修正参数名
    (
        "messages=messages,\n                delay=delay,\n                search_pages=search_pages",
        "messages=messages,\n                send_delay=delay,\n                close_wechat=False,\n"
        "                search_pages=search_pages",
    ),
    # 多好友发送：函数名修正 + 消息形状归一 + 不关闭主窗口
    (
        "Messages.send_message_to_friends(\n"
        "                friends=friends,\n"
        "                message=message\n"
        "            )",
        "Messages.send_messages_to_friends(\n"
        "                friends=friends,\n"
        "                messages=[[message]] * len(friends) if isinstance(message, str)\n"
        "                else [m if isinstance(m, list) else [m] for m in message],\n"
        "                close_wechat=False\n"
        "            )",
    ),
    # 翻页保护：定位目标日期的循环最多 300 次，避免微信窗口状态异常时无限滚动（鼠标被拖回中心）
    (
        "            search_count += 1\n"
        "            if search_count % 10 == 0:\n"
        "                self.logger.warning(f\"未找到目标日期，已翻页{search_count}次，继续查找...\")",
        "            search_count += 1\n"
        "            if search_count % 10 == 0:\n"
        "                self.logger.warning(f\"未找到目标日期，已翻页{search_count}次，继续查找...\")\n"
        "            if search_count >= 300:\n"
        "                self.logger.error(\"翻页超过300次仍未定位目标日期，放弃本次读取（微信窗口可能异常）\")\n"
        "                break",
    ),
    # 翻页保护：收集消息的循环同样限制 300 次
    (
        "            collect_count += 1\n"
        "            if collect_count % 10 == 0:\n"
        "                self.logger.info(f\"已翻页{collect_count}次，收集到{len(target_messages)}条消息，继续查找...\")",
        "            collect_count += 1\n"
        "            if collect_count % 10 == 0:\n"
        "                self.logger.info(f\"已翻页{collect_count}次，收集到{len(target_messages)}条消息，继续查找...\")\n"
        "            if collect_count >= 300:\n"
        "                self.logger.error(\"翻页超过300次，停止收集（微信窗口可能异常）\")\n"
        "                break",
    ),
    # ===== 用户要求：操作完成后自动关闭微信窗口（M7 修复轮） =====
    (
        "close_wechat: bool = False, scroll_delay: float = 0.01):",
        "close_wechat: bool = True, scroll_delay: float = 0.01):",
    ),
    (
        "messages=[message],\n                send_delay=delay,\n                close_wechat=False,\n"
        "                search_pages=search_pages",
        "messages=[message],\n                send_delay=delay,\n                close_wechat=True,\n"
        "                search_pages=search_pages",
    ),
    (
        "messages=messages,\n                send_delay=delay,\n                close_wechat=False,\n"
        "                search_pages=search_pages",
        "messages=messages,\n                send_delay=delay,\n                close_wechat=True,\n"
        "                search_pages=search_pages",
    ),
    (
        "else [m if isinstance(m, list) else [m] for m in message],\n"
        "                close_wechat=False\n"
        "            )",
        "else [m if isinstance(m, list) else [m] for m in message],\n"
        "                close_wechat=True\n"
        "            )",
    ),
    # 修复按日期查找的"同日跨页误判"：一页里同时有昨天与今天时，
    # 原逻辑遇到更早日期立即 break，导致当天消息少（如只有 1 条）时会误判"未找到"
    (
        "                    if msg_date_obj == target_date_obj:\n"
        "                        found_target_date = True\n"
        "                        break\n"
        "                    elif msg_date_obj < target_date_obj:\n"
        "                        found_earlier_date = True\n"
        "                        if not found_target_date:\n"
        "                            break",
        "                    # 补丁：遍历完整页再判断——同页可能同时含昨天与今天，不能遇到更早日期就提前放弃\n"
        "                    if msg_date_obj == target_date_obj:\n"
        "                        found_target_date = True\n"
        "                    elif msg_date_obj < target_date_obj:\n"
        "                        found_earlier_date = True",
    ),
]


def patch_file(path: Path, old: str, new: str, label: str) -> bool:
    """按精确字符串替换一处补丁；目标代码不存在时视为已打过/版本不同"""
    if not path.exists():
        print(f"[警告] {label}：文件不存在 {path}")
        return False
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"[跳过] {label}（已打过补丁或目标代码不存在）")
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"[完成] {label}")
    return True


def verify_patches() -> list[str]:
    """反向校验关键补丁是否真的生效（防止第三方包版本变化导致静默跳过）"""
    problems: list[str] = []
    if not MCP_CLIENT_FILE.exists():
        return [f"文件不存在：{MCP_CLIENT_FILE}"]

    text = MCP_CLIENT_FILE.read_text(encoding="utf-8")
    if "close_wechat: bool = True, scroll_delay: float = 0.01):" not in text:
        problems.append("读取默认 close_wechat=True 未生效")
    if text.count("send_delay=delay") < 2:
        problems.append("发送参数 send_delay 未全部生效（应为 2 处）")
    if text.count("close_wechat=True") < 3:
        problems.append("发送 close_wechat=True 未全部生效（应为 3 处）")
    if "Messages.send_messages_to_friends(" not in text:
        problems.append("多好友发送函数名修正未生效")
    if "补丁：遍历完整页再判断" not in text:
        problems.append("按日期查找的同日跨页误判修复未生效")
    # 语法检查（内存编译，不写 __pycache__）
    try:
        compile(text, str(MCP_CLIENT_FILE), "exec")
    except SyntaxError as e:
        problems.append(f"补丁后语法错误：{e}")
    return problems


def main() -> int:
    if not PYWECHAT_DIR.exists():
        print(f"未找到 pywechat 目录: {PYWECHAT_DIR}（请先安装 mcp_server_wechat）")
        return 1

    #5.pywechat 补丁
    patch_file(INIT_FILE, INIT_OLD, INIT_NEW, "pywechat/__init__.py 64位检查")
    for i, (old, new) in enumerate(TOOLS_PATCHES, 1):
        patch_file(TOOLS_FILE, old, new, f"pywechat/WeChatTools.py 进程检测 #{i}")

    #6.mcp_server_wechat 补丁
    for i, (old, new) in enumerate(MCP_CLIENT_PATCHES, 1):
        patch_file(MCP_CLIENT_FILE, old, new, f"mcp_server_wechat/WechatClient.py #{i}")

    #7.反向校验关键补丁（M7 发送能力依赖）
    problems = verify_patches()
    if problems:
        print("[校验失败] 以下补丁未生效：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("补丁校验通过")
    print("微信 MCP 依赖补丁处理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())