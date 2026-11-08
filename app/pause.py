"""手动暂停：让鲸鲸暂时不说话、不主动、不在群里冒泡。

面板上的「⏸ 暂停」按钮就是改这里。暂停不是杀进程——网页、设置、记忆、NapCat 都还在，
只是她把嘴闭上了；再点一下「▶ 继续」就恢复。

状态存在 `data/pause.json`：正在开会/睡觉时重启，她也不会突然又开口。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from .config import DATA_DIR

STATE_FILE = DATA_DIR / "pause.json"
_lock = threading.RLock()


def _read() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"[pause] 状态写不进去：{exc}")


def _load() -> dict:
    data = _read()
    if not data.get("paused"):
        return {"paused": False, "since": 0.0}
    try:
        since = float(data.get("since") or time.time())
    except (TypeError, ValueError):
        since = time.time()
    return {"paused": True, "since": since}


_state = _load()


def status() -> dict:
    with _lock:
        since = float(_state.get("since") or 0)
        return {
            "paused": bool(_state.get("paused")),
            "since": since,
            "since_text": datetime.fromtimestamp(since).strftime("%m-%d %H:%M") if since else "",
        }


def is_paused() -> bool:
    with _lock:
        return bool(_state.get("paused"))


def set_paused(on: bool) -> dict:
    """暂停 / 继续。返回和 GET /api/pause 一样的结构。"""
    with _lock:
        _state["paused"] = bool(on)
        _state["since"] = time.time() if on else 0.0
        _write({"paused": _state["paused"], "since": _state["since"]})
    return status()


def reload() -> dict:
    """重新读盘（测试用；正常运行时只有启动那一次）。"""
    global _state
    with _lock:
        _state = _load()
    return status()
