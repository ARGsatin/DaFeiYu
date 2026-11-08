"""大肥鱼的记忆：对话、长期摘要、以及一点点「宠物状态」。"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session, id);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    content TEXT NOT NULL,
    upto_id INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    session TEXT PRIMARY KEY,
    mood REAL NOT NULL DEFAULT 72,
    fullness REAL NOT NULL DEFAULT 55,
    affection REAL NOT NULL DEFAULT 10,
    updated_at REAL NOT NULL
);
"""

DEFAULT_STATE = {"mood": 72.0, "fullness": 55.0, "affection": 10.0}


class FishMemory:
    """一个进程内共享的单文件数据库，够用且好备份。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---------- 对话 ----------
    def add(self, session: str, role: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session, role, content, created_at) VALUES(?,?,?,?)",
                (session, role, content, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, session: str, limit: int = 16) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages WHERE session=? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def since(self, session: str, after_id: int = 0, limit: int = 40) -> list[dict]:
        """id 比 after_id 新的消息。网页靠它轮询「她有没有自己冒出来说话」。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content FROM messages WHERE session=? AND id>? "
                "ORDER BY id LIMIT ?",
                (session, int(after_id or 0), limit),
            ).fetchall()
        return [{"id": i, "role": r, "content": c} for i, r, c in rows]

    def last_id(self, session: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(id) FROM messages WHERE session=?", (session,)
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def count(self, session: str) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session=?", (session,)
                ).fetchone()[0]
            )

    # ---------- 长期摘要 ----------
    def summary(self, session: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM summaries WHERE session=? ORDER BY id DESC LIMIT 1",
                (session,),
            ).fetchone()
        return row[0] if row else ""

    def save_summary(self, session: str, content: str, upto_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO summaries(session, content, upto_id, created_at) VALUES(?,?,?,?)",
                (session, content, upto_id, time.time()),
            )
            self._conn.commit()

    def unsummarized(self, session: str, limit: int = 40) -> list[dict]:
        last = self._conn.execute(
            "SELECT upto_id FROM summaries WHERE session=? ORDER BY id DESC LIMIT 1",
            (session,),
        ).fetchone()
        after = last[0] if last else 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content FROM messages WHERE session=? AND id>? ORDER BY id LIMIT ?",
                (session, after, limit),
            ).fetchall()
        return [{"id": i, "role": r, "content": c} for i, r, c in rows]

    # ---------- 关于主人的零散事实 ----------
    def add_fact(self, session: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts(session, content, created_at) VALUES(?,?,?)",
                (session, content, time.time()),
            )
            self._conn.commit()

    def facts(self, session: str, limit: int = 12) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT content FROM facts WHERE session=? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [r[0] for r in reversed(rows)]

    # ---------- 状态：会随时间饿、会掉心情 ----------
    def state(self, session: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT mood, fullness, affection, updated_at FROM state WHERE session=?",
                (session,),
            ).fetchone()
            if not row:
                now = time.time()
                self._conn.execute(
                    "INSERT INTO state(session, mood, fullness, affection, updated_at) VALUES(?,?,?,?,?)",
                    (session, DEFAULT_STATE["mood"], DEFAULT_STATE["fullness"],
                     DEFAULT_STATE["affection"], now),
                )
                self._conn.commit()
                return {**DEFAULT_STATE, "updated_at": now}
        mood, fullness, affection, updated_at = row
        mood, fullness, updated_at = self._tick(mood, fullness, updated_at)
        if (mood, fullness, updated_at) != (row[0], row[1], row[3]):
            self._set(session, mood=mood, fullness=fullness, updated_at=updated_at)
        return {"mood": mood, "fullness": fullness, "affection": affection,
                "updated_at": updated_at}

    @staticmethod
    def _tick(mood: float, fullness: float, updated_at: float):
        now = time.time()
        minutes = max(0.0, (now - updated_at) / 60.0)
        if minutes < 1:
            return mood, fullness, updated_at
        fullness = max(0.0, fullness - 0.30 * minutes)
        if fullness < 30:
            mood = max(0.0, mood - 0.05 * minutes)
        else:
            mood = mood + (70 - mood) * min(0.5, 0.004 * minutes)
        return round(min(100.0, max(0.0, mood)), 2), round(fullness, 2), now

    def _set(self, session: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE state SET {cols}, updated_at=? WHERE session=?",
                (*fields.values(), fields.get("updated_at", time.time()), session),
            )
            self._conn.commit()

    def bump(self, session: str, mood: float = 0.0, fullness: float = 0.0,
             affection: float = 0.0) -> dict:
        cur = self.state(session)
        self._set(
            session,
            mood=min(100.0, max(0.0, cur["mood"] + mood)),
            fullness=min(100.0, max(0.0, cur["fullness"] + fullness)),
            affection=min(100.0, max(0.0, cur["affection"] + affection)),
        )
        return self.state(session)

    def reset_state(self, session: str) -> dict:
        self._set(session, mood=72.0, fullness=55.0, affection=10.0)
        return self.state(session)

    # ---------- 给模型看的自然语言状态 ----------
    def state_prompt(self, session: str) -> str:
        st = self.state(session)
        full, mood, aff = st["fullness"], st["mood"], st["affection"]

        if full < 15:
            belly = "你饿得快贴缸底了，说话有气无力"
        elif full < 35:
            belly = "你有点饿，注意力老飘到吃的上"
        elif full < 75:
            belly = "你不饿，状态正常"
        else:
            belly = "你吃撑了，懒得动，只想趴着"

        if mood < 25:
            feel = "心情很差，不太想理人"
        elif mood < 50:
            feel = "有点闷，不太提得起劲"
        elif mood < 80:
            feel = "心情不错"
        else:
            feel = "心情很好，话比平时多"

        if aff < 20:
            bond = "你跟主人还不算太熟"
        elif aff < 55:
            bond = "你开始习惯主人在了"
        elif aff < 85:
            bond = "你挺黏主人的"
        else:
            bond = "你离不开主人了"

        hour = time.localtime().tm_hour
        if 0 <= hour < 6:
            when = "现在是深夜，主人还没睡"
        elif hour < 11:
            when = "现在是上午"
        elif hour < 14:
            when = "现在是中午"
        elif hour < 18:
            when = "现在是下午"
        else:
            when = "现在是晚上"

        return f"【你现在的状态】{belly}；{feel}；{bond}。{when}。"
