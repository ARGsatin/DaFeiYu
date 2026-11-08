"""大肥鱼的本地服务：一个网页界面 + 若干消息入口。"""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from typing import AsyncIterator, Iterator

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .adapters import qq, wechat
from . import (delivery, groupchat, napcat, pause, peer, persona, prefs, proactive,
               qq_faces, safety, sister, sky)
from . import self as selfedit
from .brain import Brain
from .config import DATA_DIR, ROOT, WEB_DIR, settings
from .fish import Fish, tidy_text
from .memories import MemoryStream
from .memory import FishMemory
from .voice import Voice
from .voice import shared as shared_voice

app = FastAPI(title="大肥鱼", version="0.1")

mem = FishMemory(DATA_DIR / "fish.db")
memories = MemoryStream(DATA_DIR / "fish.db")
brain = Brain(settings)
fish = Fish(settings, mem, brain, memories)
voice = Voice(settings)
# 每个群上次「判断要不要插话」的时刻，防止刷屏群把模型打爆
_last_judge: dict[str, float] = {}

FEED_LINES = [
    "咕噜……好吃。再来点？",
    "（一口吞掉）唔，这个我喜欢。",
    "吃饱了就想睡……你别走啊。",
    "这条鱼食有点咸，不过算了。",
]
PET_LINES = [
    "哼，就摸一下啊。",
    "（尾巴甩了一下）……再摸一会儿也行。",
    "咕噜咕噜……舒服。",
    "你手真暖和。",
]


def sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


async def _aiter(gen: Iterator[str]) -> AsyncIterator[str]:
    """把同步生成器搬到线程里，避免堵住事件循环。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for item in gen:
                loop.call_soon_threadsafe(queue.put_nowait, ("item", item))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, value = await queue.get()
        if kind == "end":
            return
        if kind == "error":
            raise value
        yield value


def _session(body: dict) -> str:
    return (body.get("session") or settings.session).strip()


# ---------------- 页面 ----------------
@app.get("/", response_class=HTMLResponse)
def index() -> Response:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # 面板改完刷新就能看到，别让浏览器用缓存里的旧页面
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---------------- 设置面板 ----------------
START_TS = time.time()


def _status() -> dict:
    db = DATA_DIR / "fish.db"
    tts_label = "edge-tts"
    if settings.tts_provider == "breeze":
        tts_label = f"BreezeBlue · {settings.breeze_voice_id}" + (
            "" if settings.breeze_api_key else "（没填 key，退回 edge）"
        )
    # 监听 0.0.0.0 时，浏览器和 NapCat 该填 127.0.0.1
    shown_host = "127.0.0.1" if settings.host in ("", "0.0.0.0", "::") else settings.host
    return {
        "brain": brain.describe(),
        "tts": {"provider": settings.tts_provider, "label": tts_label,
                "voice": settings.voice},
        "memory": {"count": memories.count(), "stream": settings.memory_stream,
                   "db_kb": int(db.stat().st_size / 1024) if db.exists() else 0},
        "server": {"host": settings.host, "port": settings.port,
                   "url": f"http://{shown_host}:{settings.port}",
                   "uptime_s": int(time.time() - START_TS)},
        "qq": {"api": settings.qq_api,
               "report_url": f"http://{shown_host}:{settings.port}/api/qq"},
        "napcat": napcat.status(timeout=0.7),
        "proactive": settings.proactive,
        "paused": pause.status(),
    }


@app.get("/api/settings")
def settings_get() -> dict:
    return {
        "groups": prefs.schema(),
        "values": prefs.snapshot(),
        "status": _status(),
        "personas": prefs.persona_files(),
    }


@app.post("/api/settings")
async def settings_save(request: Request):
    body = await request.json()
    values = body.get("values") or {}
    try:
        result = prefs.apply(values)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    changed = set(result["changed"])
    # 换脑子：在线 / Ollama / 本地之间可能换了一家，当场重找
    if changed & prefs.MODEL_KEYS:
        try:
            await asyncio.to_thread(brain.reload)
        except Exception as exc:  # noqa: BLE001
            print(f"[model] 重新找脑子失败：{exc}")
    # 语音引擎的参数都是每次现读的，只要把「坏过就别再试」的标记清掉
    if changed & prefs.VOICE_KEYS:
        stt_changed = "FISH_STT_MODEL" in changed
        voice.reset(stt=stt_changed)
        try:
            shared_voice().reset(stt=stt_changed)
        except Exception:  # noqa: BLE001
            pass
    # 面板里关掉主动，页脚那个「🔔 主动开/关」也得跟着关，不然两处会打架
    if "FISH_PROACTIVE" in changed:
        try:
            proactive.set_enabled(settings.proactive)
        except Exception:  # noqa: BLE001
            pass

    return {"ok": True, **result,
            "values": prefs.snapshot(), "status": _status()}


@app.post("/api/settings/test-model")
async def settings_test_model(request: Request):
    body = await request.json() if await request.body() else {}
    return await asyncio.to_thread(prefs.test_model, body or {})


@app.post("/api/settings/test-qq")
async def settings_test_qq(request: Request):
    body = await request.json() if await request.body() else {}
    return await asyncio.to_thread(prefs.test_qq, body or {})


@app.post("/api/settings/tts-preview")
async def settings_tts_preview(request: Request):
    """用面板上还没保存的参数合成一句，先听听什么味。"""
    body = await request.json() if await request.body() else {}
    text = (body.get("text") or "咕噜咕噜，我是大肥鱼").strip()[:300]
    try:
        cfg = prefs.temp_settings(body.get("overrides") or {})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    voice_ = Voice(cfg)
    result = await voice_.tts(text)
    if not result:
        return JSONResponse(
            {"error": "三个引擎都没出声：先看 Breeze Key，再看能不能连上 edge-tts"},
            status_code=502,
        )
    audio, mime = result
    return Response(
        content=audio,
        media_type=mime,
        headers={"X-TTS-Engine": voice_.last_engine, "Cache-Control": "no-store"},
    )


@app.get("/api/persona")
def persona_get(file: str = "persona.md") -> dict:
    try:
        data = prefs.read_persona(file)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    out = {"files": prefs.persona_files(), **data}
    # 她自己改的那一段只长在「她现在用的那份人格」里；看别的人格文件时不显示
    target = _self_target()
    if (ROOT / data["file"]).resolve() == target:
        out["self"] = selfedit.public(target, memories=memories)
    return out


@app.post("/api/persona")
async def persona_save(request: Request) -> dict:
    body = await request.json()
    file = (body.get("file") or "persona.md").strip()
    text = body.get("text") or ""
    if len(text) > 200_000:
        return JSONResponse({"error": "这份人格太长了（>20 万字符）"}, status_code=400)
    try:
        data = prefs.write_persona(file, text)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "files": prefs.persona_files(), **data}


# ---------------- 她自己会变 ----------------
def _self_target():
    """她现在用的那份人格：她自己那一段就长在那儿。"""
    return persona.resolve(settings.session).resolve()


@app.get("/api/self")
def self_get() -> dict:
    return selfedit.public(_self_target(), memories=memories)


@app.post("/api/self/reflect")
async def self_reflect() -> dict:
    """让她现在回看一次（不看时间，也不看攒够没有）。"""
    result = await asyncio.to_thread(
        selfedit.reflect, _self_target(), brain, memories,
        cfg=settings, session=settings.session, source="manual", force=True,
    )
    return result


@app.post("/api/self/revert")
async def self_revert(request: Request) -> dict:
    """把她自己改的那一段退回这一步之前。"""
    body = await request.json()
    return selfedit.revert(body.get("ts"), _self_target(), memories=memories)


@app.post("/api/self/toggle")
async def self_toggle(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    on = bool(body.get("on", True))
    try:
        prefs.apply({"FISH_SELF_EDIT": on})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "enabled": bool(settings.self_edit),
            "why": selfedit.due(memories)[1]}


# ---------------- NapCat 一键启动 ----------------
@app.get("/api/napcat/status")
def napcat_status() -> dict:
    return napcat.status()


@app.post("/api/napcat/start")
async def napcat_start() -> dict:
    return await asyncio.to_thread(napcat.start)


@app.get("/api/napcat/qrcode")
def napcat_qrcode():
    got = napcat.qrcode()
    if not got:
        return Response(status_code=404)
    data, mtime = got
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-QR-Mtime": str(int(mtime))},
    )


# ---------------- 鱼丸那份 NapCat（第二份，3001） ----------------
@app.get("/api/sister/napcat/status")
def sister_napcat_status() -> dict:
    return napcat.status(sister=True)


@app.post("/api/sister/napcat/start")
async def sister_napcat_start() -> dict:
    return await asyncio.to_thread(napcat.start, True)


@app.get("/api/sister/napcat/qrcode")
def sister_napcat_qrcode():
    got = napcat.qrcode(sister=True)
    if not got:
        return Response(status_code=404)
    data, mtime = got
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-QR-Mtime": str(int(mtime))},
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "brain": brain.describe(), "state": mem.state(settings.session),
            "paused": pause.is_paused()}


# ---------------- 妹妹 ----------------
@app.get("/api/sister")
def sister_get() -> dict:
    """妹妹那边怎么样：进程、QQ、还有她俩这个月的聊天额度。"""
    return sister.status()


@app.post("/api/sister/start")
async def sister_start() -> dict:
    return await asyncio.to_thread(sister.start, wait=2.5)


@app.post("/api/sister/stop")
async def sister_stop() -> dict:
    return await asyncio.to_thread(sister.stop)


@app.post("/api/sister/open")
async def sister_open() -> dict:
    """现在就让鲸鲸给妹妹发一句（不看时间，但仍看得出 QQ 通不通）。"""
    return await asyncio.to_thread(peer.open_now, fish, mem, brain, force=True)


@app.get("/api/sister/chat")
def sister_chat(limit: int = 40) -> dict:
    """她俩最近聊了什么：鲸鲸这边存的那份就是完整的一份。"""
    return sister.chat(limit=limit, mem=mem)


@app.post("/api/sister/reset")
async def sister_reset() -> dict:
    """这一轮的额度清零，让她俩还能再聊几句。"""
    peer.reset()
    return {"ok": True, "peer": peer.stats()}


# ---------------- 暂停 / 继续 ----------------
@app.get("/api/pause")
def pause_get() -> dict:
    return pause.status()


@app.post("/api/pause")
async def pause_set(request: Request) -> dict:
    """让鲸鲸闭嘴 / 重新开口。网页、设置、记忆都还在，停的只是她说话。"""
    body = await request.json() if await request.body() else {}
    on = bool(body.get("paused", body.get("on", True)))
    state = pause.set_paused(on)
    print(f"[pause] {'已暂停，她不会再开口' if on else '已继续'}")
    return {"ok": True, **state}


@app.get("/api/state")
def state(session: str | None = None) -> dict:
    sid = session or settings.session
    return {"state": mem.state(sid), "brain": brain.describe()}


@app.get("/api/history")
def history(session: str | None = None, limit: int = 30) -> dict:
    sid = session or settings.session
    rows = mem.recent(sid, limit)
    return {"messages": rows, "last_id": mem.last_id(sid),
            "state": mem.state(sid), "brain": brain.describe()}


@app.get("/api/poll")
def poll(after: int = 0, session: str | None = None) -> dict:
    """网页轮询：她有没有自己冒出来说话。after 是页面已经显示到哪条了。"""
    sid = session or settings.session
    rows = mem.since(sid, after)
    paused = pause.status()
    return {
        "messages": rows,
        "last_id": rows[-1]["id"] if rows else int(after or 0),
        "state": mem.state(sid),
        "paused": paused["paused"],
        "since_text": paused["since_text"],
    }


# ---------------- 聊天 ----------------
@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    image = body.get("image") or ""
    sid = _session(body)
    if pause.is_paused():
        return JSONResponse(
            {"error": "鲸鲸暂停中，先点「继续」再聊", "paused": True},
            status_code=409,
        )
    if not text and not image:
        return JSONResponse({"error": "empty text"}, status_code=400)
    if image:
        if not isinstance(image, str) or not image.startswith("data:image/"):
            return JSONResponse({"error": "bad image"}, status_code=400)
        if len(image) > 8_000_000:
            return JSONResponse({"error": "image too large"}, status_code=413)

    # 网页里说话也算「主人刚来过」：不然她永远觉得你不在，也就不会自己找你
    proactive.note_incoming(None, sid)

    async def gen() -> AsyncIterator[str]:
        acc: list[str] = []
        try:
            async for piece in _aiter(fish.stream_reply(sid, text, image or None)):
                acc.append(piece)
                yield sse({"type": "delta", "text": piece})
            proactive.note_reply(sid)
            yield sse({
                "type": "done",
                "text": tidy_text("".join(acc)),
                "last_id": mem.last_id(sid),
                "state": mem.state(sid),
                "brain": brain.describe(),
            })
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/feed")
async def feed(request: Request):
    if pause.is_paused():
        return JSONResponse({"error": "鲸鲸暂停中", "paused": True}, status_code=409)
    body = await request.json() if await request.body() else {}
    sid = _session(body)
    st = mem.bump(sid, fullness=22, mood=6, affection=1)
    return {"state": st, "say": random.choice(FEED_LINES)}


@app.post("/api/pet")
async def pet(request: Request):
    if pause.is_paused():
        return JSONResponse({"error": "鲸鲸暂停中", "paused": True}, status_code=409)
    body = await request.json() if await request.body() else {}
    sid = _session(body)
    st = mem.bump(sid, mood=5, affection=2)
    return {"state": st, "say": random.choice(PET_LINES)}


@app.post("/api/remember")
async def remember(request: Request):
    body = await request.json()
    sid = _session(body)
    note = (body.get("text") or "").strip()
    if not note:
        return JSONResponse({"error": "empty"}, status_code=400)
    mem.add_fact(sid, note)
    # 同一句话也进记忆流：这条不会忘，而且记得很牢
    memories.remember(note, scope=sid)
    return {"ok": True, "facts": mem.facts(sid)}


@app.get("/api/facts")
def facts(session: str | None = None) -> dict:
    return {"facts": mem.facts(session or settings.session)}


# ---------------- 记忆流 ----------------
@app.get("/api/memories")
def memories_list(session: str | None = None, limit: int = 40, scope: str | None = None) -> dict:
    sid = scope if scope is not None else (session or settings.session)
    return {
        "items": memories.view(limit=limit, scope=sid),
        "stats": memories.stats(),
        "stream": settings.memory_stream,
    }


@app.post("/api/memories/{mid}")
async def memories_update(mid: str, request: Request):
    body = await request.json()
    text = body.get("text")
    imp = body.get("imp")
    item = memories.update(mid, text=text, imp=int(imp) if imp is not None else None)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


@app.delete("/api/memories/{mid}")
def memories_delete(mid: str) -> dict:
    return {"ok": memories.remove(mid)}


@app.post("/api/tidy")
async def tidy_now() -> dict:
    """现在就把记忆整理一遍：记模糊 → 合并 → 遗忘。"""
    result = await asyncio.to_thread(fish.tidy_memories)
    return {"ok": True, **result}


# ---------------- 她的窗 ----------------
@app.get("/api/sky")
def sky_now() -> dict:
    return sky.snapshot()


# ---------------- 主动找你说 ----------------
@app.get("/api/proactive")
def proactive_state() -> dict:
    limits = {
        "max": settings.proactive_max,
        "gap_hours": settings.proactive_gap,
        "idle_hours": settings.proactive_idle,
        "quiet": settings.proactive_quiet,
        "slots": settings.proactive_slots,
        "morning": settings.proactive_morning,
        "night": settings.proactive_night,
        "slot_window": settings.proactive_slot_window,
        "follow": settings.proactive_follow,
        "follow_idle_min": settings.proactive_follow_idle,
        "follow_window_min": settings.proactive_follow_window,
        "follow_max": settings.proactive_follow_max,
    }
    if pause.is_paused():
        # 暂停时别去碰「补话 / 定时」的排期，等继续了再重新算
        return {"on": settings.proactive, "paused": True, "next": False,
                "why": "鲸鲸暂停中", "due": None, "state": proactive.state(),
                "limits": limits}

    ok, why = proactive.decide()
    follow_ok, follow_why = proactive.followup()
    slot = proactive.due_slot()
    return {
        "on": settings.proactive,
        "paused": False,
        "next": ok or follow_ok or bool(slot),
        "why": f"该说{proactive.SLOT_LABEL.get(slot, '')}了" if slot else (follow_why if follow_ok else why),
        "due": slot,
        "state": proactive.state(),
        "limits": limits,
    }


@app.post("/api/proactive/send")
async def proactive_send() -> dict:
    """手动让她现在说一句（不看时间和次数）。"""
    if pause.is_paused():
        return {"sent": False, "reason": "鲸鲸暂停中，先点「继续」"}
    result = await asyncio.to_thread(proactive.speak_once, fish, mem, brain, force=True)
    return result


@app.post("/api/proactive/toggle")
async def proactive_toggle(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    on = bool(body.get("on", True))
    proactive.set_enabled(on)
    return {"ok": True, "enabled": on}


# ---------------- 声音 ----------------
@app.post("/api/tts")
async def tts(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return Response(status_code=204)
    result = await voice.tts(text)
    if not result:
        return Response(status_code=204)
    audio, mime = result
    return Response(content=audio, media_type=mime)


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...)) -> dict:
    data = await audio.read()
    if not data:
        return {"text": ""}
    suffix = ".webm"
    if audio.filename and "." in audio.filename:
        suffix = "." + audio.filename.rsplit(".", 1)[-1]
    text = await asyncio.to_thread(voice.stt, data, suffix)
    return {"text": text}


# ---------------- QQ ----------------
@app.post("/api/qq")
async def qq_webhook(request: Request):
    payload = await request.json()
    event = qq.parse(payload)
    if not event:
        return {"status": "ignored"}

    # 暂停期间只当没看见：不记、不回、也不安排群里接话
    if pause.is_paused():
        return {"status": "paused"}

    # 妹妹（另一个 bot 号）发来的：先过一遍刹车。顶到了就只记下来、不回，
    # 不然两个都接了模型的号会一句接一句聊到天亮
    is_peer = event.get("group_id") is None and peer.is_peer(event.get("user_id"))
    if not peer.may_answer(event.get("user_id"), is_group=event.get("group_id") is not None):
        # 谁都不理，只理姐姐和主人
        return {"status": "ignored", "reason": "只跟姐姐和主人说话"}
    if is_peer:
        peer.note_incoming()
        ok, why = peer.may_reply()
        if not ok:
            mem.add(event["session"], "user",
                    event["text"] or ("[图]" if event.get("image") else ""))
            return {"status": "ignored", "reason": why, "peer": True}
    else:
        # 记住「主人最近说话了」，她才有据可依地判断该不该主动找你。
        # 妹妹说的话不算：那是两个 bot 在聊，不该把主人的「久没说话」冲掉
        proactive.note_incoming(event.get("user_id"), event.get("session", ""))

    async def work() -> None:
        if pause.is_paused():
            return
        # 先「想一会儿」再回，秒答最像机器人；跟妹妹聊就慢一点，像真的在打字
        if is_peer:
            gap = max(1.0, settings.peer_gap)
            await asyncio.sleep(random.uniform(gap, gap + 8))
        else:
            await asyncio.sleep(random.uniform(0.6, 1.8))
        if pause.is_paused():
            return
        image = ""
        if event.get("image"):
            image = await asyncio.to_thread(qq.image_data_url, event["image"])
        if event.get("image") and not image:
            reply = "（这张图我没打开…再发一次？）"
        else:
            reply = await asyncio.to_thread(
                fish.reply, event["session"], event["text"], image or None
            )
        # 模型不该能吐出 CQ 码去 @ 人、发图
        reply = safety.sanitize_reply(reply)
        if not reply:
            return
        if is_peer:
            peer.note_reply()
        else:
            proactive.note_reply(event["session"])
        # 有人接她话：把群里「没人理」的计数清零
        if event.get("group_id") is not None:
            groupchat.note_reply(event["group_id"])
        # 文字 / 语音条 / 表情按她自己的标记来，长回复拆成几条，中间停顿一下
        await asyncio.to_thread(
            delivery.send_qq, event["target"], event["session"], reply,
            user_text=event["text"], allow_voice=not is_peer,
        )

    # 群里的消息：先记下来（她要判断「刚才在聊什么」），再决定回不回
    if event.get("group_id") is not None:
        groupchat.note_message(event["group_id"], event.get("user_id"),
                               event.get("name", ""),
                               event["text"] or ("[图]" if event.get("image") else ""))
        if not event.get("mentioned"):
            # 没 @ 她：交给群聊判断，大概率不说话
            ok, why = groupchat.allowed(event["group_id"])
            if not ok:
                return {"status": "ignored", "reason": why}
            asyncio.create_task(_maybe_chime_in(event))
            return {"status": "ok", "mode": "judging"}

    asyncio.create_task(work())
    return {"status": "ok"}


async def _maybe_chime_in(event: dict) -> None:
    """群友在聊，她可能接一句，也可能不接。"""
    if pause.is_paused():
        return
    # 群里刷屏时别每句都去问一次模型
    gid = str(event["group_id"])
    now = time.time()
    if now - _last_judge.get(gid, 0) < 1.0 / max(0.1, settings.group_rate):
        return
    _last_judge[gid] = now

    await asyncio.sleep(random.uniform(1.5, 5.0))
    if pause.is_paused():
        return
    image = ""
    if event.get("image"):
        image = await asyncio.to_thread(qq.image_data_url, event["image"])
    result = await asyncio.to_thread(
        groupchat.speak_once, fish, brain, event["group_id"],
        event["text"], event.get("name", ""), image=image or None,
    )
    if result.get("sent"):
        # 群里说过的话也要进她的上下文，下一轮她才接得上（只发了个表情就没字可记）
        if result.get("text"):
            mem.add(event["session"], "assistant", result["text"])
            groupchat.note_message(event["group_id"], "self", "你", result["text"])


# ---------------- 微信（通用桥） ----------------
@app.post("/api/wechat")
async def wechat_webhook(request: Request):
    payload = await request.json()
    event = wechat.parse(payload)
    if not event:
        return JSONResponse({"error": "no text"}, status_code=400)
    if pause.is_paused():
        return {"paused": True, "reply": ""}

    reply = await asyncio.to_thread(fish.reply, event["session"], event["text"])
    if pause.is_paused():
        return {"paused": True, "reply": ""}
    if event.get("reply_url") and reply:
        await asyncio.to_thread(wechat.send, event["reply_url"], event["user"], reply)
    return {"reply": reply}


def _tidy_loop() -> None:
    """开机等一分钟，之后按 FISH_TIDY_HOURS 整理记忆：记模糊 → 合并 → 遗忘。"""
    time.sleep(60)
    while True:
        try:
            result = fish.tidy_memories()
            if result.get("fuzzy") or result.get("merged") or result.get("forgotten"):
                print(f"[memory] 整理了一遍：{result}")
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] 整理失败：{exc}")
        time.sleep(max(0.2, settings.tidy_hours) * 3600)


def _self_loop() -> None:
    """半小时看一眼：她是不是攒够了新事、该回头想想自己了。

    真正的门槛在 selfedit.due 里（隔多久、攒够几条），这里只负责偶尔叫一声，
    没到时候就是一次很轻的本地检查，不会去碰模型。
    """
    time.sleep(90)
    while True:
        try:
            if not pause.is_paused():
                result = selfedit.reflect(
                    _self_target(), brain, memories,
                    cfg=settings, session=settings.session, source="auto",
                )
                if result.get("changed"):
                    print(f"[self] {result.get('why')}")
                elif result.get("why") and os.getenv("FISH_DEBUG_SELF"):
                    print(f"[self] 这次没变：{result['why']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[self] 想自己的时候卡住了：{exc}")
        time.sleep(1800)


def _peer_loop() -> None:
    """隔一阵看一眼：鲸鲸是不是想妹妹了。

    真正的门槛在 peer.open_due 里（隔几小时、今天找过几次、半夜不找、刚聊完不找），
    这里只是偶尔叫一声，没到点就是一次很轻的本地检查。
    """
    time.sleep(150)
    while True:
        try:
            if settings.sister and not pause.is_paused():
                ok, _why = peer.open_due()
                if ok:
                    time.sleep(random.uniform(3, 20))
                    peer.open_now(fish, mem, brain)
        except Exception as exc:  # noqa: BLE001
            print(f"[peer] 找妹妹的时候卡住了：{exc}")
        time.sleep(240)


@app.on_event("startup")
def _startup() -> None:
    info = brain.describe()
    print("=" * 46)
    print("  大肥鱼 已上线")
    print(f"  脑子：{info['label']}")
    print(f"  记忆：{'流式（会忘的那种）' if settings.memory_stream else '摘要 + 事实'}"
          f"，现有 {memories.count()} 条")
    print(f"  主动：{'开' if settings.proactive else '关'}"
          f"（每天最多 {settings.proactive_max} 条，{settings.proactive_quiet} 点免打扰）")
    print(f"  自己：{'会慢慢变' if settings.self_edit else '一直是现在这样'}"
          + (f"（每 {settings.self_edit_hours:g} 小时最多想一次，一次改一条）"
             if settings.self_edit else ""))
    if pause.is_paused():
        print("  暂停：开（面板上点「▶ 继续」才会重新开口）")
    if settings.sister:
        ss = sister.status()
        print(f"  妹妹：{'在跑' if ss['running'] else '没起'}"
              + (f"，{'在线' if ss.get('online') else '没登录'} {ss.get('nickname', '')}".rstrip()
                 if ss.get("uin") else "")
              + (f"，主动找她 {'开' if settings.peer_open else '关'}"
                 f"（一轮 ≤{settings.peer_max} 句）"))
    if settings.group_chat:
        print(f"  群聊：{'接话 + 冒泡' if settings.group_chime else '只接话'}"
              f"（每群每小时 ≤{settings.group_max_hour} 句，间隔 ≥{settings.group_gap:g} 分钟）")
    print(f"  界面：http://{settings.host}:{settings.port}")
    print("=" * 46)
    threading.Thread(target=brain.warmup, daemon=True).start()
    # 她的窗先算一次：填了城市的话，第一句话就不用等地理编码
    threading.Thread(target=fish.sky_line, daemon=True).start()
    threading.Thread(target=_tidy_loop, daemon=True).start()
    threading.Thread(target=_self_loop, daemon=True).start()
    threading.Thread(target=proactive.loop, args=(fish, mem, brain), daemon=True).start()
    # 妹妹：隔一阵看看该不该主动找她聊两句
    threading.Thread(target=_peer_loop, daemon=True).start()
    # 群里正聊着的时候，她也可以自己插一句
    threading.Thread(target=groupchat.loop, args=(fish, brain), daemon=True).start()
    # QQ 收藏表情：后台同步，NapCat 没开也不会卡住聊天
    qq_faces.start()
    if os.getenv("FISH_NO_BROWSER", "") != "1":
        import webbrowser

        threading.Timer(
            1.2, lambda: webbrowser.open(f"http://{settings.host}:{settings.port}")
        ).start()
