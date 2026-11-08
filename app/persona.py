"""按会话挑人设：私聊一套、群聊一套、网页一套。"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from .config import ROOT

DEFAULT_FILE = ROOT / "persona.md"
RULES_FILE = ROOT / "personas.json"

_cache: dict | None = None
_cache_mtime: float = 0.0


def _load_rules() -> dict:
    global _cache, _cache_mtime
    if not RULES_FILE.exists():
        return {}
    mtime = RULES_FILE.stat().st_mtime
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        _cache = data if isinstance(data, dict) else {}
        _cache_mtime = mtime
        return _cache
    except Exception as exc:  # noqa: BLE001
        print(f"[persona] personas.json 读不了：{exc}")
        return {}


def resolve(session: str) -> Path:
    """返回这个会话该用的人设文件。"""
    cfg = _load_rules()

    for rule in cfg.get("rules", []):
        pattern = str(rule.get("match", ""))
        if pattern and fnmatch.fnmatch(session, pattern):
            path = ROOT / str(rule.get("file", ""))
            if path.is_file():
                return path

    default = cfg.get("default")
    if default:
        path = ROOT / str(default)
        if path.is_file():
            return path

    return DEFAULT_FILE


def text_for(session: str) -> str:
    path = resolve(session)
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[persona] 读 {path} 失败：{exc}")
        return DEFAULT_FILE.read_text(encoding="utf-8") if DEFAULT_FILE.exists() else "你是大肥鱼。"


def search_hint(session: str) -> str:
    """这个会话搜索时该带什么关键词（让搜索结果更准）。"""
    cfg = _load_rules()
    table = cfg.get("search") or {}
    for pattern, hint in table.items():
        if fnmatch.fnmatch(session, pattern):
            return str(hint)
    return ""
