"""英雄联盟实时赛程：直接解析 lolesports.com 页面里嵌的数据。"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

URL = "https://lolesports.com/schedule"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
CACHE_TTL = 120  # 比分变得快，两分钟刷一次

_lock = threading.Lock()
_cache: tuple[float, str] | None = None
CST = timezone(timedelta(hours=8))


def _extract_events(html: str) -> list[dict]:
    """页面上有好几段 events 数组（赛果 / 待打各一份），按 id 合并。"""
    merged: dict[str, dict] = {}
    needle = '"events":['
    start = 0
    while True:
        i = html.find(needle, start)
        if i < 0:
            break
        j = i + len(needle) - 1
        depth, k, in_str, esc = 0, j, False, False
        while k < len(html):
            ch = html[k]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        if depth == 0 and k < len(html):
            try:
                data = json.loads(html[j : k + 1])
                if isinstance(data, list):
                    for event in data:
                        if isinstance(event, dict) and event.get("id"):
                            merged[event["id"]] = event
            except Exception:
                pass
        start = i + 1
    return list(merged.values())


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _score(event: dict) -> tuple[str, str, str]:
    teams = event.get("matchTeams") or []
    codes = []
    for t in teams[:2]:
        codes.append(t.get("code") or t.get("name") or "?")
    while len(codes) < 2:
        codes.append("?")
    wins = [str((t.get("result") or {}).get("gameWins", "")) for t in teams[:2]]
    if len(wins) == 2 and all(w.isdigit() for w in wins):
        return codes[0], f"{wins[0]}:{wins[1]}", codes[1]
    return codes[0], "vs", codes[1]


BLOCK_CN = {
    "play-ins": "入围赛",
    "swiss": "瑞士轮",
    "quarterfinals": "八强赛",
    "semifinals": "四强赛",
    "finals": "决赛",
    "group": "小组赛",
    "knockout": "淘汰赛",
}


def _label(league: str, block: str) -> str:
    cn = BLOCK_CN.get(block.strip().lower(), block)
    if cn and league:
        return f"{league} {cn}"
    return cn or league


def _build() -> str:
    try:
        html = requests.get(URL, headers={"User-Agent": UA}, timeout=25).text
        events = _extract_events(html)
    except Exception as exc:  # noqa: BLE001
        print(f"[esports] 拉取失败：{exc}")
        return ""

    if not events:
        return ""

    now = datetime.now(timezone.utc)
    live, upcoming, recent = [], [], []

    for e in events:
        if e.get("type") != "match":
            continue
        when = _parse_time(e.get("startTime") or "")
        if not when:
            continue
        state = e.get("state")
        a, mid, b = _score(e)
        if a in ("TBD", "?") and b in ("TBD", "?"):
            continue
        block = e.get("blockName") or ""
        league = (e.get("league") or {}).get("name") or ""
        label = _label(league, block)

        if state == "inProgress":
            live.append(f"{a} {mid} {b}（{label}，进行中）")
        elif state == "completed" and now - when < timedelta(days=3):
            local = when.astimezone(CST)
            today = datetime.now(CST).date()
            delta = (today - local.date()).days
            day = "今天" if delta <= 0 else ("昨天" if delta == 1 else f"{local:%m-%d}")
            recent.append((when, f"{day} {a} {mid} {b}（{label}）"))
        elif state == "unstarted" and now - timedelta(hours=2) < when < now + timedelta(days=7):
            upcoming.append((when, f"{a} vs {b}（{label}）"))

    if not (live or upcoming or recent):
        return ""

    lines = [f"【LOL 赛事实时数据（现在 {datetime.now(CST):%m-%d %H:%M} 北京时间）】"]
    if live:
        lines.append("正在打：" + "；".join(live[:3]))
    if upcoming:
        upcoming.sort(key=lambda x: x[0])
        today = datetime.now(CST).date()
        lines.append("还没开打的对阵（这些比赛都还没开始）：")
        for when, text in upcoming[:6]:
            local = when.astimezone(CST)
            day = "今天" if local.date() == today else (
                "明天" if local.date() == today + timedelta(days=1) else f"{local:%m-%d}"
            )
            lines.append(f"- {day} {local:%H:%M} {text}")
    if recent:
        recent.sort(key=lambda x: x[0], reverse=True)
        lines.append("近三天已结束的比赛：" + "；".join(t for _, t in recent[:4]))

    lines.append(
        "（这是官方赛程页的实时数据。回答时只能照抄上面的事实：队伍、比分、日期、时间。"
        "别自己加减日期、别补场次、别推测谁赢谁输、别猜谁还没打完。"
        "如果问「都有谁 / 名单 / 哪几场」，就把上面出现的队伍和对阵全部列出来。"
        "不要加推测性细节（例如「前面那场打完就轮到它」），只给事实。）"
    )
    return "\n".join(lines)


def digest() -> str:
    global _cache
    now = time.time()
    with _lock:
        if _cache and now - _cache[0] < CACHE_TTL:
            return _cache[1]
    text = _build()
    with _lock:
        _cache = (now, text)
    return text
