"""群里她也想有存在感。

以前群里只有被 @ 才回（`qq.parse` 里只要不是 @ 直接丢掉）。现在有两种开口方式：

1. **接话**：群友说话时，她判断要不要接一句。像普通群友那样——
   附和、吐槽、拆台、接梗、反问都行，不用每条都接，但别端着。
2. **冒泡**：群里一直聊着，她可能自己插一句（`loop` 里排期），
   跟私聊的「补话」是一个意思。这是「自由」的来源：不是等你问她才张口。

当然要有硬上限——同一群每小时最多 N 句、两次至少隔 GAP 分钟、每天最多 MAX 句、
连着几句没人接话就自己收声。@ 她的话不受这些限制（那是有人在跟她说话）。

⚠️ 群里刷屏是会被踢、被举报的。上限可以调，但别关掉。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime

from . import emote, pause
from .config import DATA_DIR, settings

STATE_FILE = DATA_DIR / "groupchat.json"
_lock = threading.RLock()

# 每个群留多少条最近的群聊（判断时给模型看，也用来防重复）
KEEP_PER_GROUP = 20
# 连着这么多句都没人接，就先收声
MAX_IGNORED = 3
# 收声多久后可以再试（秒）：别一路沉默到明天
IGNORED_COOLDOWN = 1800
# 群里安静超过这么久，就不算「正在聊」，她也就不冒泡了（秒）
ACTIVE_WINDOW = 900


def _read() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        print(f"[group] 状态写不进去：{exc}")


def _group_state(data: dict, group_id: str) -> dict:
    groups = data.setdefault("groups", {})
    return groups.setdefault(str(group_id), {})


def allowed(group_id: str | int) -> tuple[bool, str]:
    """这个群让不让她自由发言。`FISH_GROUP_ALLOW` 留空 = 所有群；填了只认这几个群号。"""
    if not settings.group_chat:
        return False, "群聊自由发言关着"
    raw = (settings.group_allow or "").strip()
    if not raw or raw == "*":
        return True, "所有群"
    allow = {s.strip() for s in re.split(r"[,，\s]+", raw) if s.strip()}
    if str(group_id) in allow:
        return True, "在名单里"
    return False, f"群 {group_id} 不在白名单"


def note_message(group_id: str | int, user_id, nickname: str, text: str) -> dict:
    """群里来了一句（顺便记住是谁说的、说了什么）。返回这条记录。"""
    now = time.time()
    entry = {
        "user_id": str(user_id or ""),
        "name": (nickname or "").strip()[:16],
        "text": (text or "").strip()[:120],
        "ts": now,
    }
    if not entry["text"]:
        return entry
    with _lock:
        data = _read()
        g = _group_state(data, group_id)
        recent = list(g.get("recent") or [])
        # 她说完之后有人接着说话，就算「有人理她」，别一直锁着
        if recent and str(recent[-1].get("user_id") or "") == "self" and entry["user_id"] != "self":
            g["ignored"] = 0
        recent.append(entry)
        g["recent"] = recent[-KEEP_PER_GROUP:]
        _write(data)
    return entry


def recent(group_id: str | int, n: int = 10) -> list[dict]:
    with _lock:
        g = _read().get("groups", {}).get(str(group_id), {})
        return list(g.get("recent") or [])[-n:]


def note_spoke(group_id: str | int, text: str, slot: datetime | None = None) -> None:
    """她说了一句群聊。记一笔，后面用来限流。"""
    now = time.time()
    dt = slot or datetime.now()
    day = dt.strftime("%Y-%m-%d")
    with _lock:
        data = _read()
        g = _group_state(data, group_id)
        if g.get("day") != day:
            g["day"] = day
            g["today"] = 0
            g["hour_stamp"] = 0
            g["hour_count"] = 0
        g["last_spoke"] = now
        g["last_text"] = text[:200]
        g["today"] = int(g.get("today") or 0) + 1
        g["ignored"] = int(g.get("ignored") or 0) + 1
        # 刚说过话，下一次冒泡重新排
        g.pop("chime_at", None)
        # 小时按「哪天几点」算，和 should_speak 里一致；跨天不会复用昨天的整点
        if g.get("hour_stamp") == dt.hour and g.get("day") == day:
            g["hour_count"] = int(g.get("hour_count") or 0) + 1
        else:
            g["hour_stamp"] = dt.hour
            g["hour_count"] = 1
        _write(data)


def note_reply(group_id: str | int) -> None:
    """有人接她的话了：把「没人理」的计数清零。"""
    with _lock:
        data = _read()
        g = _group_state(data, group_id)
        g["ignored"] = 0
        _write(data)


def _reset_day(g: dict, day: str) -> None:
    if g.get("day") != day:
        g["day"] = day
        g["today"] = 0
        g["hour_stamp"] = 0
        g["hour_count"] = 0


def should_speak(group_id: str | int, now: float | None = None,
                 dt: datetime | None = None, data: dict | None = None) -> tuple[bool, str]:
    """她这会儿能不能在群里接话。纯函数，好测。@ 她的不经过这里。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    if not settings.group_chat:
        return False, "群聊自由发言关着"
    data = _read() if data is None else data
    g = data.get("groups", {}).get(str(group_id), {})
    today = dt.strftime("%Y-%m-%d")
    sent_today = int(g.get("today") or 0) if g.get("day") == today else 0
    hour_count = int(g.get("hour_count") or 0) if (
        g.get("hour_stamp") == dt.hour and g.get("day") == today) else 0

    if int(g.get("ignored") or 0) >= MAX_IGNORED:
        # 没人接话，先停一会儿，过一阵再试：别一路沉默到明天
        last = float(g.get("last_spoke") or 0)
        if now - last < IGNORED_COOLDOWN:
            return False, "连着几句没人接话"
    if sent_today >= settings.group_max_day:
        return False, "今天说够了"
    if hour_count >= settings.group_max_hour:
        return False, "这个小时说够了"
    last = float(g.get("last_spoke") or 0)
    if last and now - last < settings.group_gap * 60:
        return False, "刚说过"
    return True, "可以接话"


# ---------------- 让她自己决定说不说 ----------------
SKIP = ("（不说）", "(不说)", "（不理）", "(不理)", "（沉默）", "(沉默)", "（略）", "(略)")

# 群友说话时：想接就接，别端着。以前这里写的是「大多数时候你不该接话」「能不说就不说」，
# 模型照着执行，结果群里永远一句话没有——她不是不想说，是被提示词按住了。
CUE = (
    "群里正聊着。想接就接一句，像普通群友那样：附和、吐槽、拆台、接梗、反问、说自己的看法都行。"
    "一两句、短，别客气，也别先请示。不用每条都接，也别追着同一个人说。"
    "真的没兴趣、插不上话，才只输出「（不说）」，不用解释。"
)

# 群里一直聊着、她一直看着：这时候她自己插一句
CHIME_CUE = (
    "群里刚才一直在聊，你也一直在看。现在想说点什么就说一句——"
    "接着刚才的话题、吐槽一句、问一句、想起个事，都行。一两句、短，像群友突然插嘴。"
    "不想说就只输出「（不说）」，不用解释。"
)


def build_prompt(fish, group_id: str | int, incoming: str, who: str = "",
                 session: str | None = None, mode: str = "react",
                 image: str | None = None) -> list[dict]:
    session = session or f"qq:group:{group_id}"
    lines = []
    for m in recent(group_id, 10):
        name = m.get("name") or "群友"
        lines.append(f"{name}：{m.get('text', '')[:80]}")
    history = "\n".join(lines) or "（还没人说话）"
    now = datetime.now()
    if mode == "chime":
        lead = "群里还在聊。"
        cue = CHIME_CUE
    else:
        lead = f"最新一条是 {who or '群友'} 说的。"
        cue = CUE
    ask = (
        f"现在是 {now:%m-%d %H:%M}。\n\n群里刚才在聊：\n{history}\n\n"
        f"{lead}\n\n" + cue
    )
    if image:
        # 这条消息带着图：把图一起递过去，不然她只能靠猜
        return [
            {"role": "system", "content": fish.system_prompt(session)},
            {"role": "user", "content": [
                {"type": "text", "text": ask},
                {"type": "image_url", "image_url": {"url": image}},
            ]},
        ]
    return [
        {"role": "system", "content": fish.system_prompt(session)},
        {"role": "user", "content": ask},
    ]


def compose(fish, brain, group_id: str | int, incoming: str = "", who: str = "",
            mode: str = "react", image: str | None = None) -> str | None:
    """让她想一句群聊该不该接。返回 None = 不接。"""
    try:
        text = (brain.complete(
            build_prompt(fish, group_id, incoming, who, mode=mode, image=image),
            temperature=1.0, max_tokens=80) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[group] 想不出来：{exc}")
        return None
    if not text or any(s in text for s in SKIP):
        return None
    text = text.strip().strip('"“”「」『』').strip()
    text = re.sub(r"^(鲸鲸|大肥鱼|我)\s*[：:]\s*", "", text).strip()
    # 模型偶尔会写「鲸鲸：xxx」
    if "：" in text and len(text.split("：")[0]) <= 4:
        text = text.split("：", 1)[1].strip()
    return emote.cut(text, 120) or None


# ---------------- 冒泡：群里聊着，她自己插一句 ----------------
def _chime_target(group_id: str | int, now: float | None = None) -> float:
    """这一轮她在群里打算什么时候自己插一句：GAP ~ 3×GAP 之间随机。"""
    now = time.time() if now is None else now
    with _lock:
        data = _read()
        g = _group_state(data, group_id)
        try:
            epoch = float(g.get("chime_at") or 0)
        except Exception:
            epoch = 0.0
        if epoch > 0:
            return epoch
        lo = max(1.0, settings.group_gap) * 60
        epoch = now + random.uniform(lo, lo * 3)
        g["chime_at"] = epoch
        _write(data)
        return epoch


def chime_due(group_id: str | int, now: float | None = None,
              dt: datetime | None = None, data: dict | None = None) -> tuple[bool, str]:
    """群里正聊着，她这会儿要不要自己插一句。纯函数，好测。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    if not settings.group_chat or not settings.group_chime:
        return False, "群聊自由发言关着"
    data = _read() if data is None else data
    g = data.get("groups", {}).get(str(group_id), {})
    msgs = g.get("recent") or []
    if not msgs:
        return False, "群里还没人说话"
    last_msg = float(msgs[-1].get("ts") or 0)
    if now - last_msg > ACTIVE_WINDOW:
        # 群里冷下来了：排期作废，等下次热起来重新排
        if g.get("chime_at"):
            clear_chime(group_id)
        return False, "群里没动静"

    ok, why = should_speak(group_id, now=now, dt=dt, data=data)
    if not ok:
        return False, why
    # 排期优先用传进来的状态（好测），没有才现排一个
    try:
        target = float(g.get("chime_at") or 0)
    except Exception:
        target = 0.0
    if target <= 0:
        target = _chime_target(group_id, now)
    if now < target:
        return False, "再等等"
    return True, "群里正聊着"


def clear_chime(group_id: str | int) -> None:
    with _lock:
        data = _read()
        g = _group_state(data, group_id)
        if g.pop("chime_at", None) is not None:
            _write(data)


def tick(fish, brain) -> list[str]:
    """看一眼所有群，该冒泡的就冒一个。返回这次说了话的群。"""
    spoke: list[str] = []
    if pause.is_paused():
        return spoke
    if not (settings.group_chat and settings.group_chime):
        return spoke
    for gid in list((_read().get("groups") or {}).keys()):
        ok, _why = chime_due(gid)
        if not ok:
            continue
        res = speak_once(fish, brain, gid, mode="chime")
        if res.get("sent") and res.get("text"):
            _store(fish, f"qq:group:{gid}", gid, res["text"])
            spoke.append(str(gid))
    return spoke


def loop(fish, brain) -> None:
    """后台每分钟看一眼：哪个群正聊着，该不该由她插一句。"""
    time.sleep(45)  # 开机先别急着说话
    while True:
        try:
            tick(fish, brain)
        except Exception as exc:  # noqa: BLE001
            print(f"[group] 冒泡出错：{exc}")
        time.sleep(60)


def _store(fish, session: str, group_id: str | int, text: str) -> None:
    """她说的话也进上下文，下一轮才接得上。"""
    fish.mem.add(session, "assistant", text)
    note_message(group_id, "self", "你", text)


def speak_once(fish, brain, group_id: str | int, incoming: str = "", who: str = "",
               force: bool = False, mode: str = "react",
               image: str | None = None) -> dict:
    """群聊搭话：先看限流，再让她判断要不要说，说了就记一笔。"""
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中"}
    from . import delivery, emote
    from .adapters import qq

    sid = f"qq:group:{group_id}"
    if force:
        ok, why = True, "手动"
    else:
        ok, why = should_speak(group_id)
    if not ok:
        return {"sent": False, "reason": why}
    # NapCat 没开就别想了，白调一次模型
    if not qq.alive():
        return {"sent": False, "reason": "QQ 没连上（NapCat 没开着）"}

    text = compose(fish, brain, group_id, incoming, who, mode=mode, image=image)
    if not text:
        if force:
            return {"sent": False, "reason": "她这会儿不想接话"}
        return {"sent": False, "reason": "她没什么想说的"}

    result = delivery.send_qq(
        {"type": "group", "group_id": group_id}, sid, text, user_text=incoming
    )
    if result.get("paused"):
        return {"sent": False, "reason": "鲸鲸暂停中"}
    if result.get("sent", 0) <= 0:
        return {"sent": False, "reason": "没发出去", "text": emote.strip_tags(text)}
    clean = emote.strip_tags(text)
    note_spoke(group_id, clean)
    print(f"[group] 群 {group_id} 接话：{clean[:60] or '（只发了个表情）'}")
    return {"sent": True, "text": clean, "reason": why}


def state(group_id: str | int | None = None) -> dict:
    with _lock:
        data = _read()
        if group_id is not None:
            return dict(data.get("groups", {}).get(str(group_id), {}))
        return data
