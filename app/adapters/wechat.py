"""微信接入：没有官方个人号接口，这里只提供通用桥接。

任何能把消息 POST 过来的桥（wechaty / Gewechat / 自己写的 hook）都能接：
请求体 {"text": "...", "user": "...", "reply_url": "可选"}
同步返回 {"reply": "..."}，同时如果给了 reply_url 会再主动 POST 一份。
"""
from __future__ import annotations

import requests

from ..config import settings


def parse(payload: dict) -> dict | None:
    text = (payload.get("text") or payload.get("content") or "").strip()
    if not text:
        return None
    user = payload.get("user") or payload.get("from") or payload.get("wxid") or "unknown"
    return {
        "session": f"wechat:{user}",
        "text": text,
        "user": user,
        "reply_url": payload.get("reply_url") or settings.wechat_webhook,
    }


def send(url: str, user: str, text: str) -> bool:
    if not url:
        return False
    try:
        r = requests.post(url, json={"to": user, "text": text}, timeout=15)
        return r.status_code < 400
    except Exception as exc:
        print(f"[wechat] 回发失败：{exc}")
        return False
