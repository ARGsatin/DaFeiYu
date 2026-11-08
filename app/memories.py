"""她的记忆流：经历过的事会变淡，想起来会变牢，快忘了先记模糊，最后才忘。

参考「Generative Agents」的打分召回，但不用向量模型（接口不一定有 embedding）：

- **还记得多清楚**（retention）：艾宾浩斯曲线 R = e^(-t/S)。t 是离上次想起过了几天，
  S 是这条自己的「稳定度」（天）：重要 3 的初始 S=10 天，一周后还剩一半，一个月后基本忘光。
- **重不重要**（importance）：1~5。📌 记住的事、情绪强的事更重。
- **跟眼下多相关**（relevance）：关键词命中，加上中文二字片段重合度。相关度太低的不参与排名，
  免得一条很重要但八竿子打不着的记忆每次都被想起来。

间隔效应：想起一次 S ×(1 + 2×(1−R))，**快忘光时想起涨得最多，刚想起过又想起几乎不涨**，
所以同一件事反复聊不会把它刷成永久记忆。

每晚整理一次（tidy）：
- 清楚度掉到 20% 以下的，先改写成记不太清的样子（有模型就用模型，没有就按规则抹细节），
  细节就真没了；之后再被想起，翻出来的就是模糊那版；
- 清楚度掉到 5% 以下（超过 3 倍 S 没被想起）就忘掉；
- 关于主人的事（fact）、她自己的变化（self）不忘。

**按会话分区**（scope）：私聊里记的事不会在群里被翻出来。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .safety import looks_injected

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id     TEXT PRIMARY KEY,
    ts     REAL NOT NULL,
    seen   REAL,
    scope  TEXT NOT NULL DEFAULT '',
    kind   TEXT NOT NULL,
    text   TEXT NOT NULL,
    keys   TEXT NOT NULL DEFAULT '[]',
    imp    INTEGER NOT NULL DEFAULT 3,
    emo    REAL NOT NULL DEFAULT 0,
    s      REAL NOT NULL,
    n      INTEGER NOT NULL DEFAULT 0,
    fuzzy  INTEGER NOT NULL DEFAULT 0,
    edited INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, ts);
"""

DAY = 86400.0
MAX_ENTRIES = 1500
MAX_S = 365.0  # 稳定度封顶（天）
GAIN = 2.0  # 快忘光时想起，S 最多变成 1+GAIN 倍
FUZZY_R = 0.2  # 清楚度低于这个，开始记不清
FORGET_R = math.exp(-3)  # 低于这个（t > 3S）就忘
FUZZY_MAX = 10  # 一晚最多改写几条
MERGE_AFTER_DAYS = 3.0  # 太新的先留着细节
MERGE_MIN = 3  # 至少几条才值得合并
MERGE_GROUPS = 6  # 一晚最多合几组
W_RECENCY = 1.0
W_IMPORTANCE = 1.0
W_RELEVANCE = 2.0
MIN_RELEVANCE = 0.12
NEVER_FORGET = ("fact", "self")

# 没人给情绪强度时，从字面上估一下：带这些词的多半是有感觉的事
EMO_WORDS = re.compile(
    "哭|泪|破防|感动|心疼|难过|伤心|委屈|生气|气死|讨厌|恶心|烦死|吵架|吵了|害怕|吓|"
    "激动|兴奋|太好|超喜欢|好喜欢|爱死|笑死|绝了|震撼|离谱|崩溃|后悔|想念|舍不得|想你|想你吗"
)
# 值得单独记一句的聊天：主人在说自己
PERSONAL_WORDS = re.compile(
    r"我(今天|昨天|最近|明天|下周|上周|打算|准备|想要|觉得|讨厌|喜欢|在学|在做|买了|去了|"
    r"吃了|考|面试|上班|下班|生日|名字|住|家里|养的|玩|睡|加班|病|累|高兴|难过)"
    r"|我叫|你记|记住|别忘了|答应|说好|约好"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
DETAIL_RE = re.compile(r"[A-Za-z0-9]{2,}")
QUOTE_RE = re.compile(r"《[^》]*》|「[^」]*」|“[^”]*”|『[^』]*』")


def initial_s(imp: int, emo: float = 0.0) -> float:
    """刚记下时的稳定度（天）：重要 1 → 3.3 天，重要 5 → 16.7 天；情绪越强越牢，最多翻倍。"""
    return (10.0 * imp / 3.0) * (1.0 + max(0.0, min(1.0, emo)))


def stability(entry: dict) -> float:
    """这条自己的稳定度（天）。老数据没有 S 就按以前的寿命折算。"""
    s = entry.get("s")
    if s:
        return float(s)
    return (10.0 * (entry.get("imp") or 3) * (1 + min(entry.get("n") or 0, 5))) / 3.0


def retention(entry: dict, now: float | None = None) -> float:
    """还记得多清楚，0~1。"""
    now = time.time() if now is None else now
    last = entry.get("seen") or entry.get("ts") or now
    idle_days = max(0.0, (now - float(last)) / DAY)
    return math.exp(-idle_days / max(0.05, stability(entry)))


def reinforce(s: float, r: float) -> float:
    """想起一次之后的稳定度：间隔效应，快忘光时想起涨得最多。"""
    return min(MAX_S, s * (1 + GAIN * (1 - max(0.0, min(1.0, r)))))


def emotion_of(text: str, given: float | None = None) -> float:
    """情绪强度 0~1：给了就用给的，再跟字面估的取大的。"""
    t = str(text or "")
    hits = len(EMO_WORDS.findall(t)) + min(2, len(re.findall(r"[!！]{2,}", t)))
    guess = min(1.0, hits * 0.35)
    if given is None:
        return round(guess, 2)
    return round(max(guess, max(0.0, min(1.0, float(given)))), 2)


def _grams(text: str) -> set[str]:
    out: set[str] = set()
    for run in CJK_RE.findall(str(text or "")):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i : i + 2])
    return out


def relevance(query: str, entry: dict) -> float:
    """中文二字片段的重合度，够用且不用模型。"""
    q = _grams(query)
    if not q:
        return 0.0
    t = _grams(entry.get("text", "")) | _grams(" ".join(entry.get("keys") or []))
    return len(q & t) / float(len(q))


def degrade(text: str) -> str:
    """没有模型时怎么「记模糊」：名字、标题、数字、引号里的细节都糊掉。"""
    t = QUOTE_RE.sub("某个东西", str(text or ""))
    t = DETAIL_RE.sub("某个", t)
    t = re.sub(r"[；;].*$", "", t).strip("，,。 ")
    return f"记不太清了，好像{t[:60]}" if t else "记不太清了，好像有过这么回事"


class MemoryStream:
    """所有记忆都存在同一个 fish.db 里，拷走文件夹就是搬家。"""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---------------- 读写 ----------------
    @staticmethod
    def _entry(row: sqlite3.Row) -> dict:
        e = dict(row)
        try:
            e["keys"] = json.loads(e.get("keys") or "[]")
        except Exception:
            e["keys"] = []
        return e

    def add(
        self,
        kind: str,
        text: str,
        *,
        scope: str = "",
        keys: list[str] | tuple[str, ...] = (),
        imp: int = 3,
        emo: float | None = None,
        ts: float | None = None,
    ) -> dict | None:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return None
        if looks_injected(text):
            return None
        ts = time.time() if ts is None else float(ts)
        imp = max(1, min(5, int(round(imp))))
        emo = emotion_of(text, emo)
        entry = {
            "id": f"{int(ts * 1000):x}{uuid.uuid4().hex[:10]}",
            "ts": ts,
            "seen": None,
            "scope": scope or "",
            "kind": kind or "note",
            "text": text[:200],
            "keys": [str(k) for k in keys if k],
            "imp": imp,
            "emo": emo,
            "s": initial_s(imp, emo),
            "n": 0,
            "fuzzy": 0,
            "edited": 0,
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories(id,ts,seen,scope,kind,text,keys,imp,emo,s,n,fuzzy,edited)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry["id"], entry["ts"], entry["seen"], entry["scope"], entry["kind"],
                    entry["text"], json.dumps(entry["keys"], ensure_ascii=False),
                    entry["imp"], entry["emo"], entry["s"], entry["n"], entry["fuzzy"],
                    entry["edited"],
                ),
            )
            self._conn.commit()
            self._trim_locked()
        return entry

    def remember(self, text: str, scope: str = "", imp: int = 5) -> dict | None:
        """📌 记住：关于主人的事单独一条，不忘。"""
        return self.add("fact", text, scope=scope, imp=imp, keys=["fact"])

    def all(self, scope: str | None = None) -> list[dict]:
        with self._lock:
            if scope is None:
                rows = self._conn.execute("SELECT * FROM memories").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE scope=? OR scope=''", (scope,)
                ).fetchall()
        return [self._entry(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------------- 召回 ----------------
    def recall(
        self,
        query: str = "",
        *,
        keys: list[str] | tuple[str, ...] = (),
        k: int = 3,
        scope: str = "",
        kinds: tuple[str, ...] | None = None,
        touch: bool = True,
        now: float | None = None,
    ) -> list[dict]:
        """挑最该想起来的几条。相关度太低的不参与排名。"""
        now = time.time() if now is None else now
        q = str(query or "")
        key_set = [str(x) for x in keys if x]
        picked: list[tuple[float, dict]] = []

        for e in self.all(scope=scope):
            if kinds and e["kind"] not in kinds:
                continue
            hit = any(
                k in q or (k.split(":", 1)[-1] and k.split(":", 1)[-1] in q) for k in key_set
            ) or any(k in q for k in e.get("keys") or [] if len(k) >= 2)
            rel = relevance(q, e)
            if q and not hit and rel < MIN_RELEVANCE:
                continue
            imp = (e.get("imp") or 3) / 5.0
            score = (
                W_RECENCY * retention(e, now)
                + W_IMPORTANCE * imp
                + W_RELEVANCE * (1.0 if hit else rel)
            )
            picked.append((score, e))

        picked.sort(key=lambda pair: pair[0], reverse=True)
        out = [e for _score, e in picked[: max(0, k)]]
        if touch and out:
            with self._lock:
                for e in out:
                    e["n"] = int(e.get("n") or 0) + 1
                    e["s"] = reinforce(stability(e), retention(e, now))
                    e["seen"] = now
                    self._conn.execute(
                        "UPDATE memories SET n=?, s=?, seen=? WHERE id=?",
                        (e["n"], e["s"], now, e["id"]),
                    )
                self._conn.commit()
        return out

    def recall_text(self, query: str, *, scope: str = "", k: int = 3) -> str:
        """拼成能给模型看的一小段。没想起来就是空字符串。"""
        rows = self.recall(query, scope=scope, k=k)
        if not rows:
            return ""
        lines = []
        for e in rows:
            ago = _ago(e.get("seen") or e["ts"])
            fuzzy = "（记不太清了）" if e.get("fuzzy") else ""
            lines.append(f"- {ago}{fuzzy}：{e['text']}")
        return "\n".join(lines)

    # ---------------- 面板 ----------------
    def view(self, limit: int = 40, scope: str | None = None, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.all(scope=scope)
        rows.sort(key=lambda e: e["ts"], reverse=True)
        out = []
        for e in rows[: max(0, limit)]:
            out.append({**e, "retention": round(retention(e, now), 3), "ago": _ago(e["ts"])})
        return out

    def stats(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        rows = self.all()
        by_kind: dict[str, int] = {}
        for e in rows:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        kept = [retention(e, now) for e in rows] or [0.0]
        return {
            "total": len(rows),
            "avg_retention": round(sum(kept) / len(kept), 3),
            "fuzzy": sum(1 for e in rows if e["fuzzy"]),
            "kinds": by_kind,
            "oldest_days": round(max(((now - e["ts"]) / DAY for e in rows), default=0), 1),
        }

    def update(self, mid: str, text: str | None = None, imp: int | None = None) -> dict | None:
        """改一条。改正文就等于替她想起了这件事，顺手把 S 抬一点。"""
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
            if row is None:
                return None
            e = self._entry(row)
            if text is not None:
                text = re.sub(r"\s+", " ", str(text)).strip()
                if not text:
                    return None
                e["text"] = text[:200]
                e["s"] = reinforce(stability(e), 0.5)
                e["edited"] = 1
            if imp is not None:
                e["imp"] = max(1, min(5, int(imp)))
            self._conn.execute(
                "UPDATE memories SET text=?, imp=?, s=?, seen=?, edited=? WHERE id=?",
                (e["text"], e["imp"], e["s"], e["seen"], e["edited"], mid),
            )
            self._conn.commit()
        return e

    def remove(self, mid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (mid,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------------- 整理 ----------------
    def fuzzy_candidates(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = [e for e in self.all() if e["kind"] not in NEVER_FORGET and not e["fuzzy"]]
        rows = [e for e in rows if retention(e, now) < FUZZY_R]
        rows.sort(key=lambda e: retention(e, now))
        return rows

    def apply_fuzzy(self, mid: str, text: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET text=?, fuzzy=1 WHERE id=?", (text[:200], mid)
            )
            self._conn.commit()

    def forget(self, now: float | None = None) -> int:
        """清楚度掉到 5% 以下（超过 3S 没被想起）就忘掉。"""
        now = time.time() if now is None else now
        doomed = [
            e["id"]
            for e in self.all()
            if e["kind"] not in NEVER_FORGET and retention(e, now) < FORGET_R
        ]
        if not doomed:
            return 0
        with self._lock:
            self._conn.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in doomed])
            self._conn.commit()
        return len(doomed)

    def merge_candidates(self, now: float | None = None) -> list[list[dict]]:
        """几天前的、同一种、同一周里的几条，够 3 条就值得让她自己概括成一条「印象」。"""
        now = time.time() if now is None else now
        buckets: dict[tuple, list[dict]] = {}
        for e in self.all():
            if e["kind"] not in ("chat", "note") or e["fuzzy"]:
                continue
            if (now - e["ts"]) / DAY < MERGE_AFTER_DAYS:
                continue
            week = int((e["ts"] / DAY) // 7)
            buckets.setdefault((e["scope"], e["kind"], week), []).append(e)
        groups = [sorted(v, key=lambda e: e["ts"]) for v in buckets.values() if len(v) >= MERGE_MIN]
        groups.sort(key=len, reverse=True)
        return groups[:MERGE_GROUPS]

    def merge_into(self, group: list[dict], summary: str, now: float | None = None) -> dict | None:
        """把几条合成一条 gist，比里面最牢的那条再牢一半，然后删掉原来那几条。"""
        now = time.time() if now is None else now
        summary = re.sub(r"\s+", " ", str(summary or "")).strip()
        if not summary:
            return None
        best = max(group, key=lambda e: stability(e))
        keys: list[str] = []
        for e in group:
            for k in e.get("keys") or []:
                if k not in keys:
                    keys.append(k)
        merged = self.add(
            "gist",
            summary,
            scope=best["scope"],
            keys=keys[:8],
            imp=max(int(e.get("imp") or 3) for e in group),
            emo=max(float(e.get("emo") or 0) for e in group),
            ts=best["ts"],
        )
        if merged is None:
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET s=? WHERE id=?",
                (min(MAX_S, stability(best) * 1.5), merged["id"]),
            )
            self._conn.executemany(
                "DELETE FROM memories WHERE id=?", [(e["id"],) for e in group]
            )
            self._conn.commit()
        return merged

    def tidy(
        self,
        rewrite: Callable[[list[dict]], dict[str, str]] | None = None,
        merge: Callable[[list[dict]], str | None] | None = None,
        now: float | None = None,
    ) -> dict:
        """每晚一次：记模糊 → 合并 → 遗忘。

        rewrite：一次收到一批快忘的记忆，返回 {id: 新的模糊正文}（不传就按规则抹细节）；
        merge：收到一组差不多的记忆，返回一条概括（不传就不合并）。
        """
        now = time.time() if now is None else now
        fuzzy = 0
        candidates = self.fuzzy_candidates(now)[:FUZZY_MAX]
        rewritten: dict[str, str] = {}
        if candidates and rewrite is not None:
            try:
                rewritten = rewrite(candidates) or {}
            except Exception as exc:  # noqa: BLE001
                print(f"[memory] 批量改写失败：{exc}")
        for e in candidates:
            self.apply_fuzzy(e["id"], rewritten.get(e["id"]) or degrade(e["text"]))
            fuzzy += 1

        merged = 0
        if merge is not None:
            for group in self.merge_candidates(now):
                try:
                    summary = merge(group)
                except Exception as exc:  # noqa: BLE001
                    print(f"[memory] 合并失败：{exc}")
                    summary = None
                if summary and self.merge_into(group, summary, now=now):
                    merged += 1

        forgotten = self.forget(now)
        return {"fuzzy": fuzzy, "merged": merged, "forgotten": forgotten, "kept": self.count()}

    # ---------------- 聊天 ----------------
    def note_exchange(
        self, scope: str, user_text: str, reply: str, *, now: float | None = None,
        speaker: str = "主人",
    ) -> dict | None:
        """聊完记一句。随手一句「在吗」不占地方，主人在说自己、或者情绪强才记。

        speaker：对面那个人怎么称呼。跟姐姐/妹妹这种 bot 聊天时不该记成「主人说」，
        不然两个人的话会被当成同一个人的。
        """
        u = (user_text or "").strip()
        if len(u) < 6:
            return None
        strong = bool(PERSONAL_WORDS.search(u)) or emotion_of(u) >= 0.35
        if not strong and len(u) < 15:
            return None
        imp = 4 if strong else 3
        who = (speaker or "主人").strip() or "主人"
        line = f"{who}说：{u[:110]}"
        r = (reply or "").strip()
        if r:
            line += f"；我回：{r[:60]}"
        return self.add("chat", line, scope=scope, imp=imp, ts=now)

    # ---------------- 内部 ----------------
    def _trim_locked(self) -> None:
        """塞满了先挤掉最淡的，不是最老的。"""
        n = int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        if n <= MAX_ENTRIES:
            return
        rows = [self._entry(r) for r in self._conn.execute("SELECT * FROM memories").fetchall()]
        now = time.time()
        rows.sort(key=lambda e: (e["kind"] in NEVER_FORGET, (e.get("imp") or 3) * retention(e, now)))
        drop = [e["id"] for e in rows[: n - MAX_ENTRIES]]
        self._conn.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in drop])
        self._conn.commit()


def _ago(ts: float | None, now: float | None = None) -> str:
    """「三天前」这种。"""
    if not ts:
        return "刚才"
    now = time.time() if now is None else now
    days = (now - float(ts)) / DAY
    if days < 1 / 24:
        return "刚才"
    if days < 1:
        return f"{max(1, int(days * 24))} 小时前"
    if days < 30:
        return f"{int(days)} 天前"
    if days < 365:
        return f"{int(days / 30)} 个月前"
    return f"{int(days / 365)} 年前"
