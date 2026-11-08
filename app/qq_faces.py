"""QQ 账号「收藏」里的表情包，同步到本地给她用。

NapCat 暴露了 `fetch_custom_face_detail`：能拿到收藏表情的 md5、描述和图片 URL。
这里负责：

1. 定时把这批表情下载到 `data/qq-faces/`（只下新的，收藏删了就从索引里去掉）；
2. 用 `index.json` 记每张图的标签。标签是给模型挑类别用的：
   `[表情:吃瓜]` 会优先找标签里有「吃瓜」的那张收藏图；没标签的也能通过 `[表情:QQ收藏]` 随机发；
3. 给 `app/stickers.py` 提供 `images(tag)` / `tags()`，不直接碰网络。

同步是后台线程，NapCat 没开也不会卡住聊天；下次同步会补上。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import requests

from .config import DATA_DIR, settings

DIR = DATA_DIR / "qq-faces"
INDEX = DIR / "index.json"
MAX_BYTES = 10 * 1024 * 1024
_LOCK = threading.RLock()
_last_sync = 0.0
_last_error = ""
_last_count = -1
_started = False


# ---------------- 索引 ----------------
def _load() -> dict:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDEX)
    except Exception as exc:  # noqa: BLE001
        print(f"[qq-faces] 索引写不进去：{exc}")


def _tags_from_item(item: dict) -> list[str]:
    """QQ 里给收藏表情写过的描述，拆成标签；没描述就空着。"""
    desc = str(item.get("desc") or "").strip()
    if not desc:
        return []
    parts = re.split(r"[\s,，、;；/|]+", desc)
    return [p[:12] for p in parts if p.strip()][:6]


# ---------------- NapCat ----------------
def _api(method: str, payload: dict, timeout: float = 30) -> dict:
    base = (settings.qq_api or "").rstrip("/")
    if not base:
        raise RuntimeError("QQ_API 没配")
    headers = {"Content-Type": "application/json"}
    if settings.qq_token:
        headers["Authorization"] = f"Bearer {settings.qq_token}"
    r = requests.post(base + "/" + method, json=payload, headers=headers, timeout=timeout)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {}
    if str(data.get("status", "ok")).lower() != "ok" or int(data.get("retcode", 0) or 0) != 0:
        raise RuntimeError(f"{method} retcode={data.get('retcode')}")
    return data


def _find_existing(md5: str) -> Path | None:
    try:
        for p in DIR.glob(f"{md5}.*"):
            if p.is_file() and p.suffix != ".part" and p.stat().st_size > 200:
                return p
    except OSError:
        pass
    return None


def _extension(raw: bytes, content_type: str) -> str:
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    ct = (content_type or "").lower()
    if "gif" in ct:
        return ".gif"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


def _download(md5: str, url: str) -> Path | None:
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:  # noqa: BLE001
        print(f"[qq-faces] 下载失败（{type(exc).__name__}）")
        return None
    if r.status_code != 200 or not r.content:
        print(f"[qq-faces] 下载失败（HTTP {r.status_code}）")
        return None
    if len(r.content) > MAX_BYTES:
        print(f"[qq-faces] 跳过一张超过 {MAX_BYTES // 1024 // 1024}MB 的表情")
        return None
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{md5}{_extension(r.content, r.headers.get('content-type', ''))}"
    try:
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"[qq-faces] 文件写不进去：{exc}")
        return None


def sync(force: bool = False) -> bool:
    """拉一次收藏列表。返回是否成功；失败保留旧索引。"""
    global _last_sync, _last_error
    if not settings.qq_faces:
        return False
    with _LOCK:
        if not force and time.time() - _last_sync < 600:
            return True

    try:
        data = _api("fetch_custom_face_detail", {"count": 100})
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if msg != _last_error:
            print(f"[qq-faces] 同步失败：{msg}")
            _last_error = msg
        return False

    items = data.get("data") or []
    old = _load()
    old_items = old.get("items") or {}
    new_items: dict[str, dict] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        md5 = re.sub(r"[^A-Fa-f0-9]", "", str(item.get("md5") or "")).upper()
        url = str(item.get("url") or "").strip()
        if not md5 or not url:
            continue
        old_item = old_items.get(md5) or {}
        path = _find_existing(md5) or _download(md5, url)
        if path is None:
            # 这次没下下来，但之前有文件就继续用
            if old_item.get("file") and (DIR / old_item["file"]).is_file():
                new_items[md5] = old_item
            continue
        tags = old_item.get("tags")
        if not isinstance(tags, list):
            tags = _tags_from_item(item)
        new_items[md5] = {
            "file": path.name,
            "tags": [str(t)[:12] for t in tags if str(t).strip()][:6],
            "desc": str(item.get("desc") or old_item.get("desc") or "")[:80],
            "size": path.stat().st_size,
        }

    _save({"updated": time.time(), "items": new_items})
    with _LOCK:
        _last_sync = time.time()
        _last_error = ""
    global _last_count
    if len(new_items) != _last_count:
        print(f"[qq-faces] 收藏表情已同步：{len(new_items)} 张")
        _last_count = len(new_items)
    return True


# ---------------- 给 stickers 用 ----------------
def images(tag: str = "") -> list[Path]:
    """标签匹配的收藏图；tag 为空或「QQ收藏」= 全部。"""
    tag = str(tag or "").strip()
    out: list[Path] = []
    with _LOCK:
        items = (_load().get("items") or {})
    for item in items.values():
        if not isinstance(item, dict):
            continue
        path = DIR / str(item.get("file") or "")
        if not path.is_file():
            continue
        tags = item.get("tags") or []
        if not tag or tag == "QQ收藏" or tag in tags:
            out.append(path)
    return sorted(out)


def tags() -> list[str]:
    out: set[str] = set()
    with _LOCK:
        items = (_load().get("items") or {})
    for item in items.values():
        if isinstance(item, dict):
            out.update(str(t) for t in (item.get("tags") or []) if str(t).strip())
    return sorted(out)


def count() -> int:
    return len(images())


# ---------------- 后台同步 ----------------
def _loop() -> None:
    time.sleep(3)
    while True:
        ok = sync(force=True)
        wait = max(60.0, settings.qq_faces_min * 60) if ok else 90.0
        time.sleep(wait)


def start() -> None:
    """服务启动时调一次；只开一个线程。"""
    global _started
    if _started or not settings.qq_faces:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
