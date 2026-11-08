"""把一条回复送到 QQ：文字、语音条、表情包，按顺序发，像真人一条条来。

三条发送路径（回消息、群里接话、主动找你）都走这里，省得每个地方各写一套。
语音和表情都有「每天最多几条 / 两次至少隔多久」的硬限制，免得她刷屏；
你明确说「发个语音」「来个表情」时，间隔限制让路，但每天的总量还是照算。
"""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import emote, pause, stickers
from .adapters import qq
from .config import DATA_DIR, settings
from .voice import shared as shared_voice

VOICE_DIR = DATA_DIR / "voice-out"
STATE_FILE = DATA_DIR / "emote.json"
_lock = threading.RLock()


# ---------------- 限流 ----------------
def _limits(session: str) -> dict:
    group = session.startswith("qq:group")
    # 群里人更多，减半；私聊照着配置来
    return {
        "voice_max": max(1, settings.voice_max // 2) if group else max(1, settings.voice_max),
        "voice_gap": max(60.0, settings.voice_gap * 2) if group else max(0.0, settings.voice_gap),
        "sticker_max": max(1, settings.sticker_max // 2) if group else max(1, settings.sticker_max),
        "sticker_gap": max(60.0, settings.sticker_gap * 2) if group else max(0.0, settings.sticker_gap),
    }


def _read() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"[emote] 状态写不进去：{exc}")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _session_state(data: dict, session: str) -> dict:
    st = data.setdefault(session, {})
    if st.get("day") != _today():
        st["day"] = _today()
        st["voice"] = 0
        st["sticker"] = 0
    return st


def _allowed(session: str, kind: str, explicit: bool) -> bool:
    with _lock:
        st = _session_state(_read(), session)
        lim = _limits(session)
        if int(st.get(kind) or 0) >= lim[f"{kind}_max"]:
            return False
        last = float(st.get(f"{kind}_at") or 0)
        if not explicit and last and time.time() - last < lim[f"{kind}_gap"]:
            return False
        return True


def _note(session: str, kind: str) -> None:
    with _lock:
        data = _read()
        st = _session_state(data, session)
        st[kind] = int(st.get(kind) or 0) + 1
        st[f"{kind}_at"] = time.time()
        _write(data)


# ---------------- 语音文件 ----------------
def _clean_voice_dir() -> None:
    try:
        files = [f for f in VOICE_DIR.glob("*") if f.is_file()]
        now = time.time()
        for f in files:
            if now - f.stat().st_mtime > 86400:
                f.unlink(missing_ok=True)
        files = [f for f in VOICE_DIR.glob("*") if f.is_file()]
        for f in sorted(files, key=lambda p: p.stat().st_mtime)[:-100]:
            f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _voice_file(text: str) -> Path | None:
    result = shared_voice().tts_sync(text)
    if not result:
        return None
    audio, mime = result
    suffix = ".wav" if "wav" in (mime or "") else ".mp3"
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        path = VOICE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}{suffix}"
        path.write_bytes(audio)
        _clean_voice_dir()
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"[emote] 语音文件写不进去：{exc}")
        return None


# ---------------- 发送 ----------------
def _send_text(target: dict, text: str, *, first: bool) -> int:
    sent = 0
    for i, chunk in enumerate(qq.split_message(text)):
        if i or not first:
            time.sleep(random.uniform(0.6, 1.3))
        if qq.send(target, chunk):
            sent += 1
    return sent


def _voice_candidate(parts: list[emote.Part]) -> int | None:
    """没打 [语音] 但主人点名要语音时，挑一句短的念。"""
    for limit in (40, 60):
        for i, part in enumerate(parts):
            if part.kind == "text" and 0 < len(part.text.strip()) <= limit:
                return i
    return None


def _send_sticker(target: dict, session: str, category: str) -> bool:
    choice = stickers.resolve(category, session)
    if not choice:
        return False
    kind, value = choice
    ok = qq.send_image(target, value) if kind == "image" else qq.send_face(target, int(value))
    if not ok and kind == "image":
        # 图发不出去（格式怪、路径读不到）就退回小黄脸
        fid = stickers.face_for(category)
        if fid is not None:
            ok = qq.send_face(target, fid)
    return ok


def send_qq(target: dict, session: str, text: str, *, user_text: str = "",
            allow_voice: bool = True, allow_sticker: bool = True) -> dict:
    """把一条回复发出去。返回 {sent, voice, sticker}。"""
    # 最后一道闸：暂停前已经开始生成的那句，也别在暂停后漏出去
    if pause.is_paused():
        return {"sent": 0, "voice": False, "sticker": False, "paused": True}
    text = (text or "").strip()
    if not text:
        return {"sent": 0, "voice": False, "sticker": False}

    parts = emote.parse(text)
    explicit_voice = allow_voice and settings.voice_reply and emote.wants_voice(user_text)
    explicit_sticker = allow_sticker and settings.sticker_reply and emote.wants_sticker(user_text)

    if explicit_voice and not any(p.kind == "voice" for p in parts):
        idx = _voice_candidate(parts)
        if idx is not None:
            parts[idx] = emote.Part("voice", text=parts[idx].text)
    if explicit_sticker and not any(p.kind == "sticker" for p in parts):
        parts.append(emote.Part("sticker"))

    sent = 0
    voice_done = False
    sticker_done = False

    for part in parts:
        if part.kind == "text" and part.text.strip():
            sent += _send_text(target, part.text, first=(sent == 0))

        elif part.kind == "voice":
            if sent:
                time.sleep(random.uniform(0.5, 1.0))
            blocked = (voice_done or not allow_voice or not settings.voice_reply
                       or not _allowed(session, "voice", explicit_voice))
            path = None if blocked else _voice_file(part.text)
            if path and qq.send_record(target, path):
                _note(session, "voice")
                voice_done = True
                sent += 1
                print(f"[emote] 发了一条语音：{part.text[:30]}")
            else:
                # 语音没成，别把这句话吞了，改成文字发
                sent += _send_text(target, part.text, first=(sent == 0))

        elif part.kind == "sticker":
            if sent:
                time.sleep(random.uniform(0.4, 0.8))
            blocked = (sticker_done or not allow_sticker or not settings.sticker_reply
                       or not _allowed(session, "sticker", explicit_sticker))
            if blocked:
                continue
            if _send_sticker(target, session, part.category):
                _note(session, "sticker")
                sticker_done = True
                sent += 1
                print(f"[emote] 发了一个表情：{part.category or '随机'}")
            else:
                print(f"[emote] 表情没发出去：{part.category or '随机'}")

    if not sent:
        print(f"[emote] 什么都没发出去：{text[:60]}")
    return {"sent": sent, "voice": voice_done, "sticker": sticker_done}
