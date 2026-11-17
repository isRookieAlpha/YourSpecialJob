# -*- coding: utf-8 -*-
"""
结构化中间产物 jsonl 存储（双层去重的第一层）。

每个群一个文件：{群名安全化}_wechat_auto_struct_history.jsonl（直接使用中文群名，
仅替换 Windows 文件名非法字符；不引入拼音依赖）。
作用：① 跨进程去重缓存（load_seen_hashes）；② 提取质量人工排查档案。
"""
import json
import re
from pathlib import Path

from agent.config import ROOT_DIR

#1.存储目录（已 gitignore）
HISTORY_DIR = ROOT_DIR / "agent" / "memory" / "struct_history"

#2.Windows 文件名非法字符
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\s]+')


def history_path(group_name: str, source: str = "wechat_auto") -> Path:
    """计算某群的结构化历史文件路径

    Args:
        group_name: 微信群名（可为中文）
        source: 来源标识（wechat_auto / manual_text）

    Returns:
        jsonl 文件路径（目录不自动创建）
    """
    safe_name = _ILLEGAL_RE.sub("_", group_name).strip("_") or "unknown_group"
    return HISTORY_DIR / f"{safe_name}_{source}_struct_history.jsonl"


async def load_seen_hashes(path: Path) -> set[str]:
    """读取已有记录的 content_hash 集合（文件不存在返回空集；坏行跳过）"""
    seen: set[str] = set()
    if not path.exists():
        return seen
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = record.get("content_hash")
            if h:
                seen.add(h)
    return seen


async def append_items(path: Path, records: list[dict]) -> None:
    """追加写记录（一行一条完整 JSON；父目录自动创建）"""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")