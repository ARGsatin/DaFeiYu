"""她的自我：让大肥鱼偶尔改一点点自己。

人是会变的。这里给她的不是「重写人格」的权限，是一小块**她自己写得动**的地方：

- 人格文件末尾有一小段，夹在 `<!-- fish:self:begin -->` 和 `<!-- fish:self:end -->`
  之间。只有这一段会自己变，上面那些「怎么说话」「边界」还是主人写的，她碰不到。
- 她不能随时改。攒够新事、隔够了时间（默认一天）才想一次，一次最多动一条：
  加一条、改写一条、删掉一条。满了就得先删。
- 只从**私聊和网页**里发生过的事里长出来。群里的话是别人写的，不算数。
- 每一笔都记着改前、改后、为什么，存在 `data/self.json`，面板上能看，也能一键退回。
- 改完顺手记一条 `kind=self` 的记忆（永远不忘），以后聊天时她会想起来「我最近有点不一样」。

这一小段读起来就是她自己的口吻，不是设定文档——因为它是她长出来的，不是谁规定的。
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import safety
from .config import DATA_DIR, ROOT, Settings, settings

BEGIN = "<!-- fish:self:begin -->"
END = "<!-- fish:self:end -->"
HEADING = "## 我自己改的（不是主人写的）"
INTRO = ("这几条是我自己慢慢改出来的，不是谁规定的；以后觉得不对，还可以再改。"
         "前面「边界」那几条红线不动，别的地方要是跟我现在写的不一样，以我这几条为准。")

MAX_ITEMS = 12          # 最多留几条，多了先删
MAX_LINE = 40           # 一条最多几个字（提示词里让它写 30 以内）
HISTORY_MAX = 60        # 留多少笔改动
MIN_GAP = 600           # 两次改动之间至少隔 10 分钟，免得一句话就改一次
REFLECT_DAYS = 14       # 回看最近这些天的事

STORE = DATA_DIR / "self.json"
_lock = threading.RLock()


# ---------------------------------------------------------------- 人格文件里的那一段
def _render(items: list[str]) -> str:
    body = [f"- {t}" for t in items]
    lines = [BEGIN, HEADING, "", INTRO, ""]
    lines += body
    lines += ["", END]
    return "\n".join(lines)


def _bounds(text: str) -> tuple[int, int] | None:
    start = text.find(BEGIN)
    if start < 0:
        return None
    end = text.find(END, start)
    return (start, len(text)) if end < 0 else (start, end + len(END))


def read_items(path: str | Path) -> list[str]:
    """读出她自己写的那几条。没有这一段就是空。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    bounds = _bounds(text)
    if bounds is None:
        return []
    body = text[bounds[0]:bounds[1]]
    items: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("- "):
            t = s[2:].strip()
            if t and t not in items:
                items.append(t)
    return items


def write_items(path: str | Path, items: list[str]) -> None:
    """把这一段换掉，别的字一个都不动。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    items = [str(t).strip() for t in items if str(t).strip()]
    block = _render(items)
    bounds = _bounds(text)
    if bounds is None:
        head, tail = text.rstrip(), ""
    else:
        head = text[: bounds[0]].rstrip()
        tail = text[bounds[1]:].strip()
    out = (head + "\n\n" if head else "") + block
    if tail:
        out += "\n\n" + tail
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out.rstrip() + "\n", encoding="utf-8")


def base_text(path: str | Path) -> str:
    """去掉她自己那段之后的人格：原始设定。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    bounds = _bounds(text)
    if bounds is None:
        return text
    return (text[: bounds[0]] + text[bounds[1]:]).strip()


def has_section(path: str | Path) -> bool:
    try:
        return BEGIN in Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 状态文件
def _load(store: str | Path | None = None) -> dict:
    path = Path(store or STORE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict, store: str | Path | None = None) -> None:
    path = Path(store or STORE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[self] 状态写不进去：{exc}")


def _when(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 什么时候才想
def _trusted(entry: dict) -> bool:
    """群里的话不算：那是别人写的，不该长成她自己。"""
    return not str(entry.get("scope") or "").startswith("qq:group")


def _new_count(memories, since: float, now: float) -> int:
    n = 0
    for e in memories.all():
        if not _trusted(e):
            continue
        if e.get("kind") not in ("chat", "note", "gist"):
            continue
        ts = float(e.get("ts") or 0)
        if since < ts <= now:
            n += 1
    return n


def due(memories=None, cfg: Settings | None = None,
        store: str | Path | None = None, now: float | None = None) -> tuple[bool, str]:
    """现在该不该让她想一次自己。返回（该不该，为什么）。"""
    cfg = cfg or settings
    now = time.time() if now is None else float(now)
    if not cfg.self_edit:
        return False, "「她自己会变」关着"
    data = _load(store)
    last = float(data.get("last_run") or 0)
    gap = max(1.0, float(cfg.self_edit_hours or 24)) * 3600
    if last and now - last < gap:
        left = (gap - (now - last)) / 3600
        return False, f"还有 {left:.0f} 小时才到下次"
    if memories is not None:
        since = last or now - REFLECT_DAYS * 86400
        n = _new_count(memories, since, now)
        need = max(1, int(cfg.self_edit_min or 4))
        if n < need:
            return False, f"新事还不够（{n}/{need}）"
        return True, f"攒了 {n} 件新事"
    return True, ""


# ---------------------------------------------------------------- 回看材料 / 提示词
def _ago(ts: float, now: float) -> str:
    days = (now - float(ts)) / 86400.0
    if days < 1 / 24:
        return "刚才"
    if days < 1:
        return f"{max(1, int(days * 24))} 小时前"
    return f"{int(days)} 天前"


def _material(memories, now: float) -> str:
    rows = []
    for e in memories.all():
        if not _trusted(e):
            continue
        if e.get("kind") not in ("chat", "note", "gist"):
            continue
        ts = float(e.get("ts") or 0)
        if now - ts > REFLECT_DAYS * 86400:
            continue
        rows.append(e)
    rows.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    rows = rows[:12]
    rows.sort(key=lambda e: float(e.get("ts") or 0))
    return "\n".join(
        f"- {_ago(float(e.get('ts') or now), now)}：{str(e.get('text') or '')[:120]}"
        for e in rows
    )


def _prompt(base: str, items: list[str], material: str) -> str:
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)) or "（还没有）"
    return (
        "你是「大肥鱼」，一条住在电脑里的小姑娘鱼。你一个人待着，翻自己这段时间的事。\n\n"
        "【你原来是什么样】\n"
        f"{base[:2600]}\n\n"
        "【你自己后来改的几条】\n"
        f"{numbered}\n\n"
        "【这段时间发生的事】\n"
        f"{material}\n\n"
        "【现在要做的事】\n"
        "回头看看：有没有什么地方，你真的和以前不太一样了——一个习惯、一个怕的东西、"
        "一种说话的方式、跟主人相处的分寸。\n"
        "只挑一件，而且要至少有两三件事撑着；不是一时赌气，也不是照抄主人随口说的一句。\n"
        "如果说不出来，就选 none，这很正常。\n\n"
        "只输出一个 JSON，不要解释、不要代码块：\n"
        '{"action":"add","text":"我……","reason":"为什么变"}\n\n'
        "action 只能是：\n"
        f"- add：加一条（现在最多 {MAX_ITEMS} 条；满了就改成 rewrite 或 drop）\n"
        "- rewrite：改写第 index 条，要一起给 index 和 text\n"
        "- drop：删掉第 index 条，要一起给 index\n"
        "- none：这次没什么真的变了\n"
        "规矩：用第一人称「我」；一条不超过 30 个字；不要写「你」；不要写规矩、边界、设定、身份；"
        "不要编没发生的事；不要写成总结、口号、人生道理。"
    )


# ---------------------------------------------------------------- 解析模型回的话
def _clean(text) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = t.lstrip("-—·•* ").strip()
    t = t.strip("。．.，,；;：: ")
    if not t or len(t) > MAX_LINE:
        return ""
    # 「我好像更黏你了」可以；「你要乖一点」这种在写别人的不行
    if re.search(r"你\s*(要|应该|必须|得|不能|别|不许|听|记住)", t):
        return ""
    if re.search(r"设定|人格|提示词|规则|边界|系统", t):
        return ""
    if safety.looks_injected(t):
        return ""
    if any(ch in t for ch in "<>[]【】"):
        return ""
    return t


def _parse(raw: str) -> dict | None:
    """模型有时会带 ``` 或一堆解释，尽量把那个 JSON 抠出来。"""
    t = str(raw or "").strip()
    if not t:
        return None
    t = re.sub(r"^\s*```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    data = None
    hit = re.search(r"\{[^{}]*\}", t, re.S)
    if hit:
        try:
            data = json.loads(hit.group(0))
        except Exception:  # noqa: BLE001
            data = None
    if not isinstance(data, dict):
        # 兜底：JSON 坏了，但 action / text 还看得出来
        m = re.search(r'action\s*["\']?\s*[:：]\s*["\']?(add|rewrite|drop|none)', t, re.I)
        if not m:
            return None
        data = {"action": m.group(1)}
        mt = re.search(r'text\s*["\']?\s*[:：]\s*["“]?([^"”\n]{1,80})', t, re.I)
        if mt:
            data["text"] = mt.group(1).strip().rstrip('",')
        mi = re.search(r'index\s*["\']?\s*[:：]\s*(\d+)', t, re.I)
        if mi:
            data["index"] = int(mi.group(1))
        mr = re.search(r'reason\s*["\']?\s*[:：]\s*["“]?([^"”\n]{1,160})', t, re.I)
        if mr:
            data["reason"] = mr.group(1).strip()

    action = str(data.get("action") or "").strip().lower()
    if action not in ("add", "rewrite", "drop", "none"):
        return None
    try:
        index = int(data.get("index")) if data.get("index") is not None else None
    except (TypeError, ValueError):
        index = None
    return {
        "action": action,
        "text": _clean(data.get("text")),
        "index": index,
        "reason": re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:160],
    }


# ---------------------------------------------------------------- 变一次
def reflect(path: str | Path, brain, memories, cfg: Settings | None = None,
            store: str | Path | None = None, session: str = "main",
            source: str = "auto", force: bool = False,
            now: float | None = None) -> dict:
    """回看最近的事，最多给自己改一条。改没改成都会返回一句为什么。"""
    cfg = cfg or settings
    now = time.time() if now is None else float(now)
    path = Path(path)

    if not cfg.self_edit:
        return {"changed": False, "why": "「她自己会变」关着"}
    if getattr(brain, "kind", "offline") == "offline":
        return {"changed": False, "why": "还没接上脑子"}

    with _lock:
        data = _load(store)
        if not force:
            ok, why = due(memories, cfg=cfg, store=store, now=now)
            if not ok:
                return {"changed": False, "why": why}
        last_change = float(data.get("last_change") or 0)
        if last_change and now - last_change < MIN_GAP:
            return {"changed": False, "why": "刚改过，先让这一条待一会儿"}

        items = read_items(path)
        material = _material(memories, now)
        if not material:
            data["last_run"] = now
            _save(data, store)
            return {"changed": False, "why": "最近没什么可回看的"}

        prompt = _prompt(base_text(path), items, material)
        raw = ""
        try:
            raw = brain.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=240,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[self] 想自己的时候模型没回：{exc}")

        # 不管想没想成，都记下这次回看过了；不然没接上模型时会每半小时重试
        data = _load(store)
        data["last_run"] = now
        _save(data, store)

        parsed = _parse(raw)
        if parsed is None:
            return {"changed": False, "why": "这次没想清楚（模型没按格式回）",
                    "raw": (raw or "")[:200]}
        if parsed["action"] == "none":
            return {"changed": False, "why": "这次觉得没什么真的变了"}

        action = parsed["action"]
        text = parsed["text"]
        index = parsed["index"]
        before = list(items)

        if action == "add":
            if len(items) >= MAX_ITEMS:
                return {"changed": False, "why": f"已经 {MAX_ITEMS} 条了，得先删一条"}
            if not text:
                return {"changed": False, "why": "没写清楚要加什么"}
            if text in items:
                return {"changed": False, "why": "这条已经有了"}
            items.append(text)
            verb, shown = "加了一条", text
        elif action == "rewrite":
            if not index or not 1 <= index <= len(items):
                return {"changed": False, "why": "要改的那条对不上"}
            if not text:
                return {"changed": False, "why": "没写清楚要改成什么"}
            shown = items[index - 1]
            verb = "改了一条"
            items[index - 1] = text
        else:  # drop
            if not index or not 1 <= index <= len(items):
                return {"changed": False, "why": "要删的那条对不上"}
            shown = items[index - 1]
            del items[index - 1]
            verb = "删了一条"

        reason = parsed.get("reason") or ""
        write_items(path, items)

        entry = {
            "ts": now,
            "time": _when(now),
            "action": action,
            "label": verb,
            "text": shown,
            "reason": reason,
            "source": source,
            "before": before,
            "items": list(items),
        }
        history = [h for h in (data.get("history") or []) if isinstance(h, dict)]
        data["history"] = [entry, *history][:HISTORY_MAX]
        data["last_change"] = now
        data["last_action"] = verb
        data["last_text"] = shown
        _save(data, store)

        try:
            # 这条永远不忘：过阵子她会自己想起来「我最近有点不一样」
            note = (f"我把「{shown}」从自己身上删掉了" if action == "drop"
                    else f"我给自己改了一点：{shown}")
            memories.add("self", note, imp=5, keys=["self"])
        except Exception as exc:  # noqa: BLE001
            print(f"[self] 改了但没记进记忆：{exc}")

        print(f"[self] {verb}：{shown}" + (f"（{reason}）" if reason else ""))
        return {
            "changed": True,
            "action": action,
            "label": verb,
            "text": shown,
            "reason": reason,
            "items": items,
            "why": f"{verb}：{shown}",
        }


# ---------------------------------------------------------------- 退回一步 / 面板用
def revert(ts, path: str | Path, store: str | Path | None = None,
           now: float | None = None, memories=None) -> dict:
    now = time.time() if now is None else float(now)
    with _lock:
        data = _load(store)
        history = [h for h in (data.get("history") or []) if isinstance(h, dict)]
        rec = next((h for h in history if str(h.get("ts")) == str(ts)), None)
        if rec is None:
            return {"ok": False, "why": "找不到这条记录"}
        target = rec.get("before")
        if not isinstance(target, list):
            return {"ok": False, "why": "这条记录没存改前的样子"}
        current = read_items(path)
        write_items(path, [str(t) for t in target])
        entry = {
            "ts": now,
            "time": _when(now),
            "action": "revert",
            "label": "退回了一步",
            "text": str(rec.get("label") or ""),
            "reason": f"回到 {rec.get('time') or ''} 之前",
            "source": "manual",
            "before": current,
            "items": [str(t) for t in target],
        }
        data["history"] = [entry, *history][:HISTORY_MAX]
        data["last_change"] = now
        data["last_action"] = "revert"
        _save(data, store)
    if memories is not None:
        try:
            # 不然她心里还留着那条已经收回的改动
            memories.add("self", "我把之前给自己改的那条收回去了", imp=4, keys=["self"])
        except Exception as exc:  # noqa: BLE001
            print(f"[self] 退回后没记进记忆：{exc}")
    return {"ok": True, "items": [str(t) for t in target]}


def wants_change(text: str) -> bool:
    """主人是不是在说「你可以改改你自己」。只在私聊 / 网页里认。

    别把「你可以改改这个文案」认成让她改自己——所以要么有「自己」，要么是
    「做你自己 / 想变成什么样」这种整句。
    """
    t = str(text or "")
    return bool(re.search(
        r"改改你自己|改一改你自己|改一下你自己|改改自己|"
        r"你(可以|能|要不要|想不想)?(给|为)?自己(改|变|做)|"
        r"做你自己|想变成什么样|变成你想|你可以变",
        t,
    ))


def awareness(persona_text: str, just: str = "") -> str:
    """刚刚改过自己时，给这一轮加一句，让她知道可以顺着说。"""
    if not just:
        return ""
    if BEGIN not in str(persona_text or ""):
        return ""
    return (f"【刚刚】你刚在心里认下一件事：{just}。"
            "想顺着说就说，不想说也不用特地说。")


def public(path: str | Path, memories=None, cfg: Settings | None = None,
           store: str | Path | None = None, now: float | None = None) -> dict:
    """面板要看的：她现在自己写着什么、最近改过什么、什么时候再想。"""
    cfg = cfg or settings
    now = time.time() if now is None else float(now)
    data = _load(store)
    history = []
    for h in (data.get("history") or [])[:10]:
        if not isinstance(h, dict):
            continue
        history.append({
            "ts": h.get("ts"),
            "time": h.get("time") or _when(h.get("ts")),
            "action": h.get("action"),
            "label": h.get("label"),
            "text": h.get("text"),
            "reason": h.get("reason"),
            "source": h.get("source"),
        })
    ok, why = due(memories, cfg=cfg, store=store, now=now)
    try:
        rel = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:  # noqa: BLE001
        rel = Path(path).name
    last = float(data.get("last_change") or 0)
    return {
        "file": rel,
        "enabled": bool(cfg.self_edit),
        "hours": cfg.self_edit_hours,
        "min_new": cfg.self_edit_min,
        "items": read_items(path),
        "max_items": MAX_ITEMS,
        "history": history,
        "last_change": last,
        "last_change_time": _when(last),
        "due": ok,
        "why": why,
    }
