# -*- coding: utf-8 -*-
"""
SSE 端到端探针：POST /api/chat/stream，打印事件序列（需后端已启动）。

用法：python scripts/probe_sse.py ["问题文本"]
"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "http://127.0.0.1:8000/api/chat/stream"


def main() -> int:
    message = sys.argv[1] if len(sys.argv) > 1 else "帮我查一下 2026-11-05 佣金 100 左右的兼职"
    body = json.dumps({"thread_id": None, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )

    current_event = None
    counts: dict[str, int] = {}
    tokens: list[str] = []
    t0 = time.time()

    def stamp() -> str:
        return f"{time.time() - t0:6.1f}s"

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            print(f"HTTP {resp.status} | 请求开始计时")
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    counts[current_event] = counts.get(current_event, 0) + 1
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if current_event == "token":
                        try:
                            tokens.append(json.loads(data)["content"])
                        except Exception:
                            pass
                    elif current_event in ("meta", "todos", "tool", "error", "done"):
                        print(f"[{stamp()}] [{current_event}] {data[:220]}")
    except Exception as e:
        print(f"请求失败：{type(e).__name__}: {e}")
        return 1

    print("\n事件计数:", counts)
    print("\n拼回的回复（前 500 字）:\n", "".join(tokens)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())