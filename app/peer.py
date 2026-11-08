"""鲸鲸和妹妹在 QQ 上聊天时的那套刹车。

两个都接了模型的号放一起，会一句接一句停不下来：半夜还在聊、账单一起涨、
真主人插话都插不进去。所以这里只管三件事：

1. **认人**：只有 `FISH_PEER_UIN`（对面那个 bot）的私聊走这套规矩，别人照常；
2. **刹车**：一轮最多回几句、聊够了歇几分钟、一天封顶多少。顶到了就装没看见——
   消息照样记进上下文（下次接得上），只是不回；等冷却过了再理她；
3. **先开口**：只有鲸鲸这边会主动找妹妹（`FISH_PEER_OPEN`），妹妹只答应、不缠人。
   隔几小时才想她一次，半夜不找。

状态在 `data/peer.json`，重启不清零。所有判断都能传 `now` / `dt`，方便测。
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime

from . import emote, pause
from .config import DATA_DIR, settings

STATE_FILE = DATA_DIR / "peer.json"
_lock = threading.RLock()

# 隔这么久没说话，就算新的一轮，额度重新算
BURST_WINDOW_MIN = 45


# ---------------------------------------------------------------- 认人
def peer_uin() -> str:
    """对面那个 bot 的 QQ。妹妹那边由启动器写进 FISH_PEER_UIN；
    鲸鲸这边直接用「妹妹的 QQ」（FISH_SISTER_UIN）。"""
    uin = (settings.peer_uin or "").strip()
    if uin:
        return uin
    if settings.sister and settings.sister_uin:
        return settings.sister_uin.strip()
    return ""


def session_for(uin: str | None = None) -> str:
    return f"qq:private:{uin or peer_uin()}"


def is_peer(user_id) -> bool:
    p = peer_uin()
    return bool(p) and str(user_id or "") == p


def is_peer_session(session: str) -> bool:
    return bool(session) and session == session_for()


def may_answer(user_id, *, is_group: bool = False) -> bool:
    """这个人的消息要不要理。

    群聊照旧。私聊时，妹妹那份数据（`FISH_PEER_ONLY=1`）只认两个人：
    一个是姐姐（另一个 bot 号），一个是主人自己的 QQ（`FISH_OWNER_UIN`）。
    别人一律不理——小号到处回陌生人容易被举报。
    """
    if is_group:
        return True
    uid = str(user_id or "")
    if is_peer(uid):
        return True
    if not settings.peer_only:
        return True
    owner = (getattr(settings, "owner_uin", "") or "").strip()
    return bool(owner) and uid == owner


def recall_scopes(session: str) -> list[str]:
    """翻记忆时该看哪几个会话。

    记忆按会话分区是防止别人私聊里的事串出来；但「我跟妹妹聊过的事」也是我自己的
    经历——跟主人聊天时想不起来，就会出现「她说她不知道鱼丸是谁」这种怪事。
    群里不加：免得把私聊内容带进群。
    """
    if not session:
        return []
    scopes = [session]
    ps = session_for()
    if ps and ps != session and not is_peer_session(session) \
            and not session.startswith("qq:group"):
        scopes.append(ps)
    return scopes


def relationship_hint() -> str:
    """往系统提示里塞一句「你俩是谁」，省得她忘了自己有妹妹/姐姐。

    记忆是按会话分区的，光靠回想很容易答「不知道鱼丸是谁」；身份这件事不该靠运气。
    """
    uin = peer_uin()
    if not uin:
        return ""
    if settings.sister:
        return (f"【你妹妹】鱼丸（QQ {uin}）跟你住在同一台电脑里，是另一个你——另一个号、"
                "另一份记忆。你们在 QQ 上是一个号跟一个号聊天：她叫你姐姐，你叫她妹妹"
                "（小名丸丸，她不喜欢被叫「小鱼丸」）。跟主人提起她，就说「我妹妹鱼丸」。")
    return (f"【你姐姐】鲸鲸（QQ {uin}）跟你住在同一台电脑里，是另一个你——另一个号、"
            "另一份记忆。你叫她姐姐，她叫你妹妹（丸丸）。跟主人提起她，就说「我姐鲸鲸」。")


# ---------------------------------------------------------------- 状态
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
        print(f"[peer] 状态写不进去：{exc}")


def _bucket(data: dict) -> dict:
    key = peer_uin() or "_"
    st = data.setdefault(key, {})
    if not isinstance(st, dict):
        st = {}
        data[key] = st
    return st


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _roll(st: dict) -> None:
    today = _today()
    if st.get("day") != today:
        st["day"] = today
        st["day_count"] = 0
        st["open_day"] = 0


def quiet_now(dt: datetime | None = None) -> bool:
    """她俩的免打扰时段，默认 23 点到 9 点（妹妹比主人睡得早）。"""
    dt = dt or datetime.now()
    raw = (settings.peer_quiet or "23-9").strip()
    try:
        start_s, end_s = raw.split("-", 1)
        start, end = int(start_s), int(end_s)
    except Exception:
        start, end = 23, 9
    h = dt.hour
    if start > end:
        return h >= start or h < end
    return start <= h < end


# ---------------------------------------------------------------- 记一笔
def note_incoming(now: float | None = None) -> None:
    """妹妹说话了（不管回不回，先记下来）。"""
    now = time.time() if now is None else now
    with _lock:
        data = _read()
        st = _bucket(data)
        _roll(st)
        last = float(st.get("last_in") or 0)
        if not last or now - last > BURST_WINDOW_MIN * 60:
            st["burst_start"] = now
            st["burst_count"] = 0
        st["last_in"] = now
        _write(data)


def note_reply(now: float | None = None) -> None:
    """回了一句：这一轮、这一天各记一笔。"""
    now = time.time() if now is None else now
    with _lock:
        data = _read()
        st = _bucket(data)
        _roll(st)
        st["burst_count"] = int(st.get("burst_count") or 0) + 1
        st["day_count"] = int(st.get("day_count") or 0) + 1
        st["last_out"] = now
        _write(data)


def note_open(now: float | None = None) -> None:
    """鲸鲸先开口找她了：也算这一轮的第一句，主动次数也跟着记。"""
    now = time.time() if now is None else now
    with _lock:
        data = _read()
        st = _bucket(data)
        _roll(st)
        st["burst_start"] = now
        st["burst_count"] = 1
        st["day_count"] = int(st.get("day_count") or 0) + 1
        st["open_day"] = int(st.get("open_day") or 0) + 1
        st["last_out"] = now
        st["last_open"] = now
        _write(data)


def reset() -> None:
    """把这一轮的账清掉（面板上「重置」用）。"""
    with _lock:
        data = _read()
        st = _bucket(data)
        st.pop("mute_until", None)
        st["burst_count"] = 0
        _write(data)


# ---------------------------------------------------------------- 该不该回
def may_reply(now: float | None = None) -> tuple[bool, str]:
    """收到对面那个 bot 的消息，现在能不能回。"""
    now = time.time() if now is None else now
    with _lock:
        data = _read()
        st = _bucket(data)
        _roll(st)

        if not peer_uin():
            return False, "还没配对面的 QQ 号"
        if pause.is_paused():
            return False, "鲸鲸暂停中"

        mute = float(st.get("mute_until") or 0)
        if mute > now:
            left = int((mute - now) / 60) + 1
            return False, f"刚聊够了，歇 {left} 分钟"

        if int(st.get("day_count") or 0) >= max(1, settings.peer_day):
            return False, "今天跟妹妹聊够了"

        if int(st.get("burst_count") or 0) >= max(1, settings.peer_max):
            st["mute_until"] = now + max(1.0, settings.peer_cooldown) * 60
            _write(data)
            return False, "这一轮聊够了，过会儿再理她"

        return True, "可以回"


def open_due(now: float | None = None, dt: datetime | None = None) -> tuple[bool, str]:
    """鲸鲸这边：该不该现在主动找妹妹说句话。"""
    now = time.time() if now is None else now
    dt = dt or datetime.now()
    with _lock:
        data = _read()
        st = _bucket(data)
        _roll(st)

        if not settings.peer_open:
            return False, "主动找妹妹关着"
        if not peer_uin():
            return False, "还没配妹妹的 QQ 号"
        if pause.is_paused():
            return False, "鲸鲸暂停中"
        if quiet_now(dt):
            return False, "免打扰时段"

        if float(st.get("mute_until") or 0) > now:
            return False, "刚聊够了，先歇一会儿"
        if int(st.get("open_day") or 0) >= max(1, settings.peer_open_day):
            return False, "今天找她够多了"

        last_in = float(st.get("last_in") or 0)
        if last_in and now - last_in < BURST_WINDOW_MIN * 60:
            return False, "刚聊过"

        last_open = float(st.get("last_open") or 0)
        gap = max(0.0, settings.peer_open_gap) * 3600
        if last_open and now - last_open < gap:
            hours = (gap - (now - last_open)) / 3600
            return False, f"再等 {hours:.1f} 小时"

        if last_open:
            return True, f"上次找她是 {(now - last_open) / 3600:.1f} 小时前"
        return True, "今天还没找过她"


# ---------------------------------------------------------------- 说什么
OPEN_CUE = (
    "你现在有点无聊，想跟妹妹说句话。给她发一条：可以问她干嘛、说你在做什么、"
    "吐槽主人、或者接着刚才聊的事往下说。一句就够，短，别每次都同一个由头，"
    "别提「好久没聊」这种数字。别写成查岗的「在吗」。只输出你要发出去的那句话。"
)


def build_open_prompt(fish, mem, session: str) -> list[dict]:
    now = datetime.now()
    recent = mem.recent(session, 8)
    history = "\n".join(
        f"{'妹妹' if m['role'] == 'user' else '你'}：{m['content'][:60]}" for m in recent
    )
    ask = (
        f"现在是 {now:%m-%d %H:%M}（{'周一二三四五六日'[now.weekday()]}）。\n\n"
        f"你们上次聊的：\n{history or '（还没怎么聊过）'}\n\n{OPEN_CUE}"
    )
    return [
        {"role": "system", "content": fish.system_prompt(session)},
        {"role": "user", "content": ask},
    ]


def clean_text(text: str, limit: int = 200) -> str:
    text = (text or "").strip().strip('"“”「」『』').strip()
    text = re.sub(r"^(鲸鲸|鱼丸|大肥鱼|我)\s*[：:]\s*", "", text).strip()
    return emote.cut(text, limit) or ""


def compose(fish, mem, brain, session: str) -> str | None:
    try:
        text = brain.complete(build_open_prompt(fish, mem, session),
                              temperature=1.0, max_tokens=120)
    except Exception as exc:  # noqa: BLE001
        print(f"[peer] 想不出来：{exc}")
        return None
    return clean_text(text) or None


# ---------------------------------------------------------------- 发出去
def open_now(fish, mem, brain, *, force: bool = False) -> dict:
    """主动给妹妹发一句。force=True 是面板上手动点的，不看时间和次数。"""
    from . import delivery
    from .adapters import qq

    uin = peer_uin()
    if not uin:
        return {"sent": False, "reason": "还没配妹妹的 QQ 号"}
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中"}

    if not force:
        ok, why = open_due()
        if not ok:
            return {"sent": False, "reason": why}
    else:
        why = "手动"

    if not qq.alive():
        return {"sent": False, "reason": "QQ 没连上（NapCat 没开着）"}

    session = session_for(uin)
    text = compose(fish, mem, brain, session)
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中"}
    if not text:
        return {"sent": False, "reason": "她这会儿没什么想跟妹妹说的"}

    # 妹妹那边是另一个号，发出去就算「说过」；不进 delivery 的语音/表情额度，短一点就好
    sent = delivery.send_qq({"type": "private", "user_id": uin}, session, text,
                            allow_voice=False)
    if sent.get("paused"):
        return {"sent": False, "reason": "鲸鲸暂停中"}
    if sent.get("sent", 0) <= 0:
        return {"sent": False, "reason": "QQ 没发出去（妹妹的号在线吗）"}

    clean = emote.strip_tags(text)
    mid = mem.add(session, "assistant", clean) if clean else None
    note_open()
    print(f"[peer] 找妹妹说话（{why}）：{clean[:60]}")
    return {"sent": True, "text": clean, "reason": why, "id": mid}


# ---------------------------------------------------------------- 给面板看
def stats() -> dict:
    with _lock:
        st = _bucket(_read())
    now = time.time()
    last_in = float(st.get("last_in") or 0)
    last_out = float(st.get("last_out") or 0)
    mute = float(st.get("mute_until") or 0)
    return {
        "uin": peer_uin(),
        "open_on": settings.peer_open,
        "max": settings.peer_max,
        "burst_used": int(st.get("burst_count") or 0),
        "day_used": int(st.get("day_count") or 0) if st.get("day") == _today() else 0,
        "day_max": settings.peer_day,
        "open_used": int(st.get("open_day") or 0) if st.get("day") == _today() else 0,
        "open_max": settings.peer_open_day,
        "muted_for_min": max(0, int((mute - now) / 60)) if mute > now else 0,
        "last_in_ago_min": int((now - last_in) / 60) if last_in else None,
        "last_out_ago_min": int((now - last_out) / 60) if last_out else None,
    }
