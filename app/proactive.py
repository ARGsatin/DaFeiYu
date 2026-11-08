"""她会主动找你说话。

人跟人聊天不是「你一句、我一句」这么整齐的：话说到一半你走神了，过一会儿她又凑过来补一句；
半天没动静，她想起你；早上醒了、晚上困了，各自有一句话。这里管的就是这几个时刻。

1. **什么时候说**（全是硬规矩，宁可不说，也别变成定时问候机器人）
   - 补一句（follow）：刚聊完，她最后说了一句，你十几分钟没接话。一轮沉默里最多补一次，
     一天最多两三条；连着两次你都没回，她就不追了。
   - 想起来找你（reach）：你超过 IDLE 小时没说话，距上次主动 ≥ GAP，一天不超过 MAX 条。
   - 早晚定时（slot）：早安 / 晚安，在窗口里随机挑一个时刻发。
2. **说什么**：一次模型调用，带上人设、饿不饿、心情、窗外、最近几轮聊天和记忆，
   让她先想「这会儿有没有想说的」；真的没有就输出「（不说）」，这一轮不发。
3. **怎么送**：QQ 走 NapCat 私聊；网页界面里的会话直接写进对话库，网页轮询就能看到。
   不管发到哪儿都记一笔——以前 QQ 上说过的话没入库，她下次根本不记得自己说过。

状态存在 `data/proactive.json`：重启之后不会因为「不记得刚说过」而连发。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime

from . import emote, pause, peer
from .config import DATA_DIR, settings

STATE_FILE = DATA_DIR / "proactive.json"
_lock = threading.RLock()

# 连着几条主动都没回音，当天先别说了
MAX_SILENT_STREAK = 3
# 补话连着两次都没回，就别追了
MAX_FOLLOW_STREAK = 2
# 超过这么久「这茬」就过去了，交给 reach 那套
FOLLOW_GIVE_UP_MIN = 180


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
        print(f"[proactive] 状态写不进去：{exc}")


def state() -> dict:
    with _lock:
        return _read()


def set_enabled(on: bool) -> None:
    with _lock:
        data = _read()
        data["enabled"] = bool(on)
        _write(data)


def note_incoming(user_id: int | str | None, session: str = "") -> None:
    """你说了一句话：记下「最近活跃」，把没回音的计数清零，这一轮沉默重新算。"""
    now = time.time()
    with _lock:
        data = _read()
        data["last_seen"] = now
        data["last_session"] = session or data.get("last_session", "")
        if user_id:
            data["last_user_id"] = str(user_id)
        data["silent_streak"] = 0
        data["follow_streak"] = 0
        data["date"] = datetime.now().strftime("%Y-%m-%d")
        # 新的这一轮沉默，补话的计划重新摇
        data.pop("follow_at", None)
        data.pop("follow_sent_at", None)
        _write(data)


def note_reply(session: str = "") -> None:
    """她回完一句了。没回上话就别补话——那不像补话，像自言自语。"""
    with _lock:
        data = _read()
        data["last_reply_at"] = time.time()
        if session:
            data["last_session"] = session
        _write(data)


def note_outgoing(text: str, slot: str | None = None, follow: bool = False) -> None:
    """记一笔发出去的话。定时问候（slot）不占次数，也不进没回音的计数。"""
    now = time.time()
    with _lock:
        data = _read()
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            data["date"] = today
            data["sent_today"] = 0
            data["follow_today"] = 0
        data["last_sent"] = now
        data["last_text"] = text[:200]
        if slot:
            data[f"{slot}_done"] = today
            # 刚说完早安/晚安，别再顺着你上一句话补一句，那成了连发
            data["follow_sent_at"] = now
        else:
            data["sent_today"] = int(data.get("sent_today") or 0) + 1
            data["silent_streak"] = int(data.get("silent_streak") or 0) + 1
            if follow:
                data["follow_today"] = int(data.get("follow_today") or 0) + 1
                data["follow_streak"] = int(data.get("follow_streak") or 0) + 1
                data["follow_sent_at"] = now
        _write(data)


def note_follow_skipped() -> None:
    """这次她想补但没什么想说的：把这一轮标掉，别一分钟问一次模型。"""
    with _lock:
        data = _read()
        data["follow_sent_at"] = float(data.get("last_seen") or time.time())
        _write(data)


def _quiet_now(now: datetime | None = None) -> bool:
    """免打扰时段，默认 23 点到 8 点。"""
    now = now or datetime.now()
    raw = (settings.proactive_quiet or "23-8").strip()
    try:
        start_s, end_s = raw.split("-", 1)
        start, end = int(start_s), int(end_s)
    except Exception:
        start, end = 23, 8
    h = now.hour
    if start > end:  # 跨零点：23-8
        return h >= start or h < end
    return start <= h < end


def _qq_session(session: str) -> bool:
    return session.startswith("qq:")


def decide(now: float | None = None, data: dict | None = None,
           dt: datetime | None = None) -> tuple[bool, str]:
    """要不要现在开口（久没说话那种）。返回（说不说，为什么）。纯函数，好测。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    data = _read() if data is None else data

    if not settings.proactive:
        return False, "主动发消息关着"
    if data.get("enabled") is False:
        return False, "面板上关掉了"
    if _quiet_now(dt):
        return False, "免打扰时段"
    session = data.get("last_session") or ""
    if not session:
        return False, "还没聊过"
    # 最后说话的是妹妹：那是两个 bot 的对话，交给 peer 那套刹车管，不占「找主人」的额度
    if peer.is_peer_session(session):
        return False, "刚才是在跟妹妹聊"
    if _qq_session(session) and not data.get("last_user_id"):
        return False, "还不知道发给谁"
    if data.get("date") == dt.strftime("%Y-%m-%d") and int(data.get("sent_today") or 0) >= settings.proactive_max:
        return False, "今天说够了"
    if int(data.get("silent_streak") or 0) >= MAX_SILENT_STREAK:
        return False, "连着说了几次都没回音"
    last_sent = float(data.get("last_sent") or 0)
    if last_sent and now - last_sent < settings.proactive_gap * 3600:
        return False, "刚说过"
    last_seen = float(data.get("last_seen") or 0)
    hours = (now - last_seen) / 3600 if last_seen else 999
    if hours < settings.proactive_idle:
        return False, "刚聊过"
    return True, f"主人已经 {hours:.1f} 小时没说话了"


def _follow_target(data: dict, last_seen: float) -> float:
    """这一轮沉默里，她打算什么时候补一句：15~40 分钟之间随机，选好就存着。"""
    plan = data.get("follow_at") or {}
    try:
        anchor = float(plan.get("anchor") or 0)
        epoch = float(plan.get("epoch") or 0)
    except Exception:
        anchor, epoch = 0.0, 0.0
    if epoch and abs(anchor - last_seen) < 1:
        return epoch
    lo = max(1.0, settings.proactive_follow_idle) * 60
    hi = lo + max(0.0, settings.proactive_follow_window) * 60
    epoch = last_seen + random.uniform(lo, hi)
    with _lock:
        cur = _read()
        cur["follow_at"] = {"anchor": last_seen, "epoch": epoch}
        _write(cur)
    return epoch


def followup(now: float | None = None, data: dict | None = None,
             dt: datetime | None = None) -> tuple[bool, str]:
    """聊到一半你没了下文，她要不要自己补一句。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    data = _read() if data is None else data

    if not settings.proactive or not settings.proactive_follow:
        return False, "主动发消息关着"
    if data.get("enabled") is False:
        return False, "面板上关掉了"
    if _quiet_now(dt):
        return False, "免打扰时段"
    session = data.get("last_session") or ""
    if not session:
        return False, "还没聊过"
    # 妹妹那条线不补话：她俩的节奏由 peer 管，别一股脑跟着补
    if peer.is_peer_session(session):
        return False, "刚才是在跟妹妹聊"
    if _qq_session(session) and not data.get("last_user_id"):
        return False, "还不知道发给谁"
    last_seen = float(data.get("last_seen") or 0)
    if not last_seen:
        return False, "还没聊过"
    idle = (now - last_seen) / 60
    if idle < settings.proactive_follow_idle:
        return False, "还聊得热乎"
    if idle > FOLLOW_GIVE_UP_MIN:
        return False, "这茬过去了"
    if float(data.get("last_reply_at") or 0) < last_seen:
        return False, "她还没回上话"
    if float(data.get("follow_sent_at") or 0) >= last_seen:
        return False, "这轮已经补过"
    if int(data.get("follow_streak") or 0) >= MAX_FOLLOW_STREAK:
        return False, "补了两次都没回"
    if int(data.get("silent_streak") or 0) >= MAX_SILENT_STREAK:
        return False, "连着说了几次都没回音"
    today = dt.strftime("%Y-%m-%d")
    follow_today = int(data.get("follow_today") or 0) if data.get("date") == today else 0
    if follow_today >= settings.proactive_follow_max:
        return False, "今天补够了"
    if now < _follow_target(data, last_seen):
        return False, "再等等"
    return True, f"主人 {idle:.0f} 分钟没接话"


# ---------------- 早晚定时 ----------------
# slot 名 → (配置里几点开始, 会在这一段里随机一个时刻发)
SLOTS = {
    "morning": "08:00",
    "night": "22:00",
}
SLOT_LABEL = {"morning": "早安", "night": "晚安"}
# 过了这个窗口还没发就不发了（比如早上十一点才开机，别在中午补一句早安）
SLOT_GRACE_MIN = 90


def _slot_start(slot: str, dt: datetime) -> datetime | None:
    raw = (settings.proactive_morning if slot == "morning" else settings.proactive_night).strip()
    try:
        hh, mm = raw.split(":")
        start = dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        return None
    return start


def slot_plan(slot: str, dt: datetime, data: dict) -> float:
    """这一天这个时段打算几点发：在开始的窗口里随机挑一分钟，选好就存着，别每次问都变。"""
    today = dt.strftime("%Y-%m-%d")
    saved = data.get(f"{slot}_at") or {}
    if saved.get("date") == today and saved.get("epoch"):
        return float(saved["epoch"])
    start = _slot_start(slot, dt)
    if start is None:
        return 0.0
    epoch = start.timestamp() + random.uniform(0, settings.proactive_slot_window * 60)
    with _lock:
        cur = _read()
        cur[f"{slot}_at"] = {"date": today, "epoch": epoch}
        _write(cur)
    return epoch


def due_slot(now: float | None = None, data: dict | None = None,
             dt: datetime | None = None) -> str | None:
    """现在该不该发早安/晚安。纯查询，不改状态；发了之后由 note_outgoing 标记。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    data = _read() if data is None else data
    if not settings.proactive or not settings.proactive_slots:
        return None
    if data.get("enabled") is False:
        return None
    if not data.get("last_session"):
        return None
    # 早晚问候是给主人的；最后说话的是妹妹就别往妹妹那边发早安
    if peer.is_peer_session(data.get("last_session") or ""):
        return None
    if _qq_session(data.get("last_session") or "") and not data.get("last_user_id"):
        return None
    today = dt.strftime("%Y-%m-%d")
    for slot in SLOTS:
        if data.get(f"{slot}_done") == today:
            continue
        target = slot_plan(slot, dt, data)
        if not target:
            continue
        if target <= now <= target + SLOT_GRACE_MIN * 60 and not _quiet_now(dt):
            return slot
    return None


# ---------------- 说什么 ----------------
SKIP = ("（不说）", "(不说)", "（不想说）", "(不想说)")

FOLLOW_CUE = (
    "你们刚聊到一半，主人忽然没声了。补一句，像随口又想起什么："
    "可以自己接着刚才的话说下去、吐槽他半天不回、把话头递回去，"
    "但别问「在吗」「人呢」这种查岗的话，也别重复你上一句。一两句，短。"
    "真的没什么想说的才输出「（不说）」。"
)


def build_prompt(fish, mem, session: str, data: dict, slot: str | None = None,
                 kind: str | None = None) -> list[dict]:
    """让模型先想「现在有没有想说的」。没有就让它交白卷，别硬凑。"""
    now = datetime.now()
    last_seen = float(data.get("last_seen") or 0)
    gap = (time.time() - last_seen) / 3600 if last_seen else None
    idle_min = (time.time() - last_seen) / 60 if last_seen else None
    recent = mem.recent(session, 8)
    history = "\n".join(f"{'主人' if m['role'] == 'user' else '你'}：{m['content'][:60]}" for m in recent)

    if slot == "morning":
        cue = (
            "这一条是早上的问候：跟他说声早安。可以带上外面天色/冷不冷、问他今天打算干嘛、"
            "或者撒个娇说刚醒。一两句，别写成小作文，别问一连串问题。"
            "除非真的不想理他，否则别交白卷。"
        )
    elif slot == "night":
        cue = (
            "这一条是睡前的：跟他说晚安。可以提一句今天聊过的、让他早点睡、说你也困了。"
            "一两句，别黏太久，他要睡了。除非真的不想理他，否则别交白卷。"
        )
    elif kind == "follow":
        cue = FOLLOW_CUE
    else:
        cue = (
            "现在轮到你主动一次：想想有没有想跟他说的（想他了、撒个娇、说你在干嘛、饿了困了、"
            "外面天色、想起他之前说过的事、想给他看点什么）。"
            "有就只写你要发出去的那句话，一两句，像平时聊天那样；"
            "别报「几个小时没理我」这种数字，也别讲道理、别查岗，别每次都拿同一个由头（比如每句都说天黑）。"
            "**真的没什么想说的才只输出「（不说）」**——别动不动就交白卷，你平时挺黏人的。"
        )

    if kind == "follow" and idle_min is not None:
        when = f"主人已经 {idle_min:.0f} 分钟没接话了。"
    elif gap and gap < 999:
        when = f"主人上次跟你说话是 {gap:.1f} 小时前。"
    else:
        when = "主人有一阵子没来了。"

    ask = (
        f"现在是 {now:%m-%d %H:%M}（{'周一二三四五六日'[now.weekday()]}）。"
        + when
        + ("\n上次你主动跟他说的是「" + str(data["last_text"])[:40] + "」，这次别重复。"
           if data.get("last_text") else "")
        + "\n\n你们最近聊的：\n" + (history or "（还没聊过几句）")
        + "\n\n" + cue
    )
    return [
        {"role": "system", "content": fish.system_prompt(session)},
        {"role": "user", "content": ask},
    ]


def compose(fish, mem, brain, session: str, data: dict, slot: str | None = None,
            kind: str | None = None) -> str | None:
    try:
        msgs = build_prompt(fish, mem, session, data, slot, kind)
        text = (brain.complete(msgs, temperature=1.0, max_tokens=120) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[proactive] 想不出来：{exc}")
        return None
    if not text or any(s in text for s in SKIP):
        return None
    text = text.strip().strip('"“”「」『』').strip()
    # 有的模型会自带「鲸鲸：」这种前缀
    text = re.sub(r"^(鲸鲸|大肥鱼|我)\s*[：:]\s*", "", text).strip()
    return emote.cut(text, 200) or None


# ---------------- 发出去 ----------------
def speak_once(fish, mem, brain, *, force: bool = False, slot: str | None = None,
               kind: str | None = None) -> dict:
    """发一条。force=True 是面板上手动点的，不看时间规则；
    slot 是早/晚定时；kind="follow" 是补一句，其余是久没说话那种。"""
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中"}
    from .adapters import qq

    data = _read()
    if force:
        ok, why = True, "手动"
    elif slot:
        ok, why = True, f"{SLOT_LABEL.get(slot, slot)}定时"
    elif kind == "follow":
        ok, why = followup(data=data)
    else:
        ok, why = decide(data=data)
    if not ok:
        return {"sent": False, "reason": why}

    # 你最后在哪儿跟她说话，就发回哪儿：QQ 走私聊，网页写进库等你轮询
    session = data.get("last_session") or settings.session
    uid = data.get("last_user_id")
    to_qq = _qq_session(session)
    if to_qq:
        if not uid:
            return {"sent": False, "reason": "还不知道发给谁（等主人先说话了）"}
        # NapCat 没开就先别想了：不然每一分钟都要白调一次模型
        if not qq.alive():
            return {"sent": False, "reason": "QQ 没连上（NapCat 没开着）"}

    text = compose(fish, mem, brain, session, data, slot, kind)
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中"}
    if not text:
        # 定时 / 补话的那条，就算她不想说也标记掉，不然一分钟问一次模型，白花钱
        if slot:
            note_outgoing("（这条没说出来）", slot=slot)
        elif kind == "follow":
            note_follow_skipped()
        return {"sent": False, "reason": "她这会儿没什么想说的"}
    if to_qq:
        from . import delivery

        # 她自己换的行（或者句子太长）就拆开送，像真人连发两条
        sent = delivery.send_qq({"type": "private", "user_id": uid}, session, text)
        if sent.get("paused"):
            return {"sent": False, "reason": "鲸鲸暂停中"}
        if sent.get("sent", 0) <= 0:
            return {"sent": False, "reason": "QQ 没发出去（NapCat 开着吗）",
                    "text": emote.strip_tags(text)}
    clean = emote.strip_tags(text)
    # 说出去的话也是聊天的一部分：存下来，下一轮她才接得上，网页也能看见
    mid = mem.add(session, "assistant", clean) if clean else None
    note_outgoing(clean or "（发了个表情）", slot=slot, follow=(kind == "follow"))
    print(f"[proactive] {why}：{clean[:60] or '（只发了个表情）'}")
    return {"sent": True, "text": clean, "reason": why, "id": mid}


def loop(fish, mem, brain) -> None:
    """后台每分钟看一眼该不该开口：早晚定时 → 补一句 → 久没说话。"""
    time.sleep(30)  # 开机先别急着说话
    while True:
        try:
            if settings.proactive and not pause.is_paused():
                slot = due_slot()
                kind = None
                if slot:
                    speak_once(fish, mem, brain, slot=slot)
                else:
                    ok, _why = followup()
                    kind = "follow"
                    if not ok:
                        ok, _why = decide()
                        kind = "reach"
                    if ok:
                        # 随机抖一下，别每次都在整点
                        time.sleep(random.uniform(1.5, 10) if kind == "follow"
                                   else random.uniform(2, 20))
                        speak_once(fish, mem, brain, kind=kind)
        except Exception as exc:  # noqa: BLE001
            print(f"[proactive] 出错了：{exc}")
        time.sleep(60)
