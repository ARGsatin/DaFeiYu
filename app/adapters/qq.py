"""QQ 接入：同时兼容 OneBot v11（NapCat / LLOneBot 等）的上报格式。"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import requests

from ..config import settings

CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
CQ_IMG_RE = re.compile(r"\[CQ:image,([^\]]*)\]")
_SPLIT_RE = re.compile(r"(?<=[。！？!?~…\n])")


def split_message(text: str, max_parts: int = 3, max_len: int = 45) -> list[str]:
    """把一段话拆成几条发，像真人打字那样。

    她自己敲的换行就是「这句话想单独发」——哪怕很短也照拆；
    没换行才按标点和长度切。
    """
    text = (text or "").strip()
    if not text:
        return []
    if "\n" in text:
        parts: list[str] = []
        for line in text.split("\n"):
            parts.extend(_split_one(line.strip(), max_len))
        return _cap(parts, max_parts)
    return _split_one(text, max_len)


def _split_one(text: str, max_len: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    pieces = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    merged: list[str] = []
    for p in pieces:
        if merged and len(merged[-1]) + len(p) <= max_len:
            merged[-1] += p
        else:
            merged.append(p)

    out: list[str] = []
    for m in merged:
        while len(m) > int(max_len * 1.6):
            out.append(m[:max_len])
            m = m[max_len:]
        if m:
            out.append(m)

    out = [p.strip().rstrip("。").strip() for p in out]
    return [p for p in out if p]


def _cap(out: list[str], max_parts: int) -> list[str]:
    out = [p for p in out if p]
    if len(out) > max_parts:
        out = out[: max_parts - 1] + ["".join(out[max_parts - 1:])]
    return out


def _text_of(message) -> str:
    if isinstance(message, str):
        return CQ_RE.sub("", message).strip()
    parts: list[str] = []
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") == "text":
            parts.append((seg.get("data") or {}).get("text", ""))
    return "".join(parts).strip()


def _cq_attr(body: str, key: str) -> str:
    m = re.search(rf"(?:^|,){key}=([^,\]]*)", body or "")
    return (m.group(1) if m else "").strip()


def _image_of(message) -> dict | None:
    """从消息里挑一张图：返回 {file, url}。

    `file` 是 NapCat 的图片标识，拿它问 `/get_image` 能直接落到本机缓存；`url` 只当兜底。
    """
    if isinstance(message, str):
        m = CQ_IMG_RE.search(message)
        if not m:
            return None
        body = m.group(1)
        file_ref = _cq_attr(body, "file")
        url = _cq_attr(body, "url")
        return {"file": file_ref, "url": url} if (file_ref or url) else None
    for seg in message or []:
        if not isinstance(seg, dict) or seg.get("type") != "image":
            continue
        data = seg.get("data") or {}
        file_ref = str(data.get("file") or "").strip()
        url = str(data.get("url") or "").strip()
        if file_ref or url:
            return {"file": file_ref, "url": url}
    return None


def _sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _napcat_image(ref: str) -> tuple[Path | None, str]:
    r"""问 NapCat 这张图的本机缓存路径。

    QQ 早把图下到本机了，`/get_image` 返回的是 `D:\...\nt_data\Pic\...\xxx.png` 这种真实路径，
    读一下 0.0x 秒；从腾讯 CDN 现下经常要几十秒甚至超时。拿不到就退 URL。
    """
    base = (settings.qq_api or "").rstrip("/")
    if not base or not ref:
        return None, ""
    try:
        r = requests.post(base + "/get_image", json={"file": ref}, headers=_headers(), timeout=8)
        data = (r.json() or {}).get("data") or {}
        p = Path(str(data.get("file") or ""))
        ok = p.is_file() and p.stat().st_size > 0
        return (p if ok else None), str(data.get("url") or "")
    except Exception:
        return None, ""


def _fetch_bytes(ref) -> bytes | None:
    """按「本机文件 → NapCat 缓存 → 网络」的顺序取图。

    `ref` 可以是图片段里的 {file, url}，也可以是一串文件名 / URL / base64://。
    """
    if isinstance(ref, dict):
        name = str(ref.get("file") or "").strip()
        url = str(ref.get("url") or "").strip()
    else:
        s = str(ref or "").strip()
        name = "" if s.startswith(("http://", "https://", "base64://")) else s
        url = s if s.startswith(("http://", "https://")) else ""

    if isinstance(ref, str) and ref.startswith("base64://"):
        return base64.b64decode(ref[len("base64://"):])

    if name:
        p = Path(name[7:] if name.startswith("file://") else name)
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p.read_bytes()
        except OSError:
            pass

    local, got_url = _napcat_image(name or url)
    if local is not None:
        try:
            if local.stat().st_size > 0:
                return local.read_bytes()
        except OSError:
            pass

    target = got_url or url
    if target:
        for attempt in range(2):
            try:
                r = requests.get(target, timeout=(5, 20))
                r.raise_for_status()
                return r.content
            except Exception as exc:  # noqa: BLE001  这家的 CDN 很爱超时，再试一次
                print(f"[qq] 图下载失败（第 {attempt + 1} 次：{type(exc).__name__}）")
    return None


def image_data_url(ref, max_bytes: int = 8_000_000) -> str:
    """把 NapCat 的图片引用变成 data URL。

    优先读本机缓存（`/get_image` 给的路径），实在没有才走网络；
    拿不到就返回空串，让调用方决定怎么兜。
    """
    if not ref:
        return ""
    try:
        raw = _fetch_bytes(ref)
    except Exception as exc:  # noqa: BLE001
        print(f"[qq] 图没取到（{type(exc).__name__}）")
        return ""
    if not raw or len(raw) > max_bytes:
        print(f"[qq] 图太大或为空（{len(raw) if raw else 0} 字节）")
        return ""
    return f"data:{_sniff_mime(raw)};base64," + base64.b64encode(raw).decode()


def _mentions_me(message, self_id) -> bool:
    """群里这条消息有没有 @ 到机器人自己。"""
    target = str(self_id)
    if isinstance(message, str):
        return f"[CQ:at,qq={target}]" in message
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") == "at":
            if str((seg.get("data") or {}).get("qq", "")) == target:
                return True
    return False


def _name_of(payload: dict) -> str:
    """群友的显示名：群名片优先，其次昵称。"""
    sender = payload.get("sender") or {}
    return (sender.get("card") or sender.get("nickname") or "").strip()[:16]


def parse(payload: dict) -> dict | None:
    """把上报事件变成 {session, text, target, ...}；不是消息事件就返回 None。

    群里的返回多一个 `mentioned` 字段：
    - 被 @ 了 → mentioned=True，照常回；
    - 没被 @ → mentioned=False，也**返回**（不再直接丢掉），
      交给服务端判断要不要「插一句」。回不回由 groupchat 决定。
    """
    if payload.get("post_type") != "message":
        return None

    message = payload.get("message") or payload.get("raw_message") or ""
    text = _text_of(message)
    text = text.replace(f"@{payload.get('self_id')}", "").strip()
    image = _image_of(message)
    if not text and not image:
        return None

    mentioned = False
    gid = None
    if payload.get("message_type") == "group":
        mentioned = _mentions_me(message, payload.get("self_id"))
        gid = payload.get("group_id")
        uid = payload.get("user_id")
        session = f"qq:group:{gid}"
        reply_target = {"type": "group", "group_id": gid}
        # 自己说的话也会上报（reportSelfMessage）；别再让她回自己
        if str(uid) == str(payload.get("self_id")):
            return None
    else:
        uid = payload.get("user_id")
        session = f"qq:private:{uid}"
        reply_target = {"type": "private", "user_id": uid}

    return {"session": session, "text": text, "target": reply_target,
            "user_id": payload.get("user_id"), "mentioned": mentioned,
            "group_id": gid, "image": image,
            "name": _name_of(payload)}


def alive(timeout: float = 1.5) -> bool:
    """NapCat 通不通。发之前先问一句，省得每分钟白调一次模型。"""
    base = (settings.qq_api or "").rstrip("/")
    if not base:
        return False
    headers = {"Authorization": f"Bearer {settings.qq_token}"} if settings.qq_token else {}
    try:
        r = requests.get(base + "/get_status", headers=headers, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.qq_token}"} if settings.qq_token else {}


def _target(target: dict) -> tuple[str, dict]:
    if target.get("type") == "group":
        return "send_group_msg", {"group_id": target.get("group_id")}
    return "send_private_msg", {"user_id": target.get("user_id")}


def _post(method: str, payload: dict, timeout: float = 20) -> dict | None:
    base = (settings.qq_api or "").rstrip("/")
    if not base:
        return None
    try:
        r = requests.post(base + "/" + method, json=payload,
                          headers=_headers(), timeout=timeout)
        data = r.json() if r.content else {}
        retcode = data.get("retcode", 0)
        ok = (r.status_code == 200
              and str(data.get("status", "ok")).lower() == "ok"
              and int(retcode or 0) == 0)
        if not ok:
            print(f"[qq] {method} 失败：{r.status_code} {r.text[:240]}")
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"[qq] {method} 异常：{exc}")
        return None


def send(target: dict, text: str) -> bool:
    if not text:
        return False
    method, payload = _target(target)
    payload["message"] = text
    return _post(method, payload) is not None


def send_segment(target: dict, segment: dict, timeout: float = 30) -> bool:
    method, payload = _target(target)
    payload["message"] = [segment]
    return _post(method, payload, timeout=timeout) is not None


def _file_ref(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except Exception:  # noqa: BLE001
        return str(path)


def _b64_ref(path: Path) -> str:
    return "base64://" + base64.b64encode(path.read_bytes()).decode()


def send_image(target: dict, path: str | Path) -> bool:
    """发一张图（表情包）。本地文件先走路径，NapCat 读不到再退成 base64。"""
    p = Path(path)
    if not p.is_file():
        return False
    if send_segment(target, {"type": "image", "data": {"file": _file_ref(p)}}):
        return True
    try:
        return send_segment(target, {"type": "image", "data": {"file": _b64_ref(p)}})
    except Exception as exc:  # noqa: BLE001
        print(f"[qq] 表情读不出来：{exc}")
        return False


def send_record(target: dict, path: str | Path) -> bool:
    """发语音条。mp3 / wav 都行，NapCat 会自己转成它要的格式。"""
    p = Path(path)
    if not p.is_file():
        return False
    if send_segment(target, {"type": "record", "data": {"file": _file_ref(p)}},
                    timeout=60):
        return True
    try:
        return send_segment(target, {"type": "record", "data": {"file": _b64_ref(p)}},
                            timeout=60)
    except Exception as exc:  # noqa: BLE001
        print(f"[qq] 语音读不出来：{exc}")
        return False


def send_face(target: dict, face_id: int | str) -> bool:
    """发 QQ 自带的小黄脸。"""
    return send_segment(target, {"type": "face", "data": {"id": str(face_id)}})
