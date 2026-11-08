"""QQ 上的语音条和表情包：模型在回复里留个标记，这里把它拆成真的东西。

标记只有两种，写起来不费劲：

- `[语音]`：加在哪一句末尾，哪一句就用声音说（整条回复最多一条）；
- `[表情:类别]`：单独一行（或者句子末尾），发一张那一类的表情包。

标记本身不会发出去。模型忘了加也没关系：你明说「发个语音」「来个表情」的时候，
`wants_voice` / `wants_sticker` 会兜底，把第一句变成语音、在末尾补一个表情。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import stickers
from .config import settings

# `[语音]` / 【语音】 / [表情:无语] / [贴纸：乐] / [face:赞]
TAG_RE = re.compile(
    r"[\[【]\s*(?:"
    r"(语音|voice)(?:\s*[:：]\s*[^\]】]*)?"
    r"|"
    r"(表情|贴纸|sticker|face)\s*(?:[:：]\s*([^\]】]*))?"
    r")\s*[\]】]",
    re.IGNORECASE,
)

_VOICE_NO = re.compile(r"(?:别|不要|不用|不想|禁止|甭|少).{0,4}(?:语音|声音|歌|表情|图)")
_VOICE_ASK = re.compile(
    r"语音条|(?:发|来|整|录|搞)(?:一|个|条|段|句)?[^。！？!?\n]{0,8}?语音"
    r"|说句话|唱[^。！？!?\n]{0,3}(?:歌|一句|一段)"
    r"|喊[^。！？!?\n]{0,4}(?:一句|一声|一嗓子)"
    r"|念[^。！？!?\n]{0,4}(?:一句|一段)"
    r"|听听[^。！？!?\n]{0,4}声音|出声"
)
_STICKER_ASK = re.compile(
    r"表情包|(?:发|来|整|斗)(?:一|个|张|套)?[^。！？!?\n]{0,6}?表情|斗图|发个图"
)


@dataclass
class Part:
    kind: str          # text / voice / sticker
    text: str = ""
    category: str = ""


def parse(text: str) -> list[Part]:
    """把带标记的回复拆成按顺序发送的段落。"""
    parts: list[Part] = []
    voice_used = False
    sticker_used = False
    pending_voice = False

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        tags = list(TAG_RE.finditer(line))
        clean = TAG_RE.sub("", line).strip()
        clean = re.sub(r"[ \t]{2,}", " ", clean)

        # 模型把 [语音] 单独放一行：要么回头把上一句变成语音，要么算下一句的
        only_voice = bool(tags) and not clean and all(t.group(1) for t in tags)
        if only_voice:
            if (not voice_used and parts and parts[-1].kind == "text"
                    and 0 < len(parts[-1].text) <= 60):
                parts[-1] = Part("voice", text=parts[-1].text)
                voice_used = True
            else:
                pending_voice = True
            continue

        if (pending_voice or any(t.group(1) for t in tags)) and not voice_used:
            if clean and len(clean) <= 60:
                parts.append(Part("voice", text=clean))
                voice_used = True
                clean = ""
        pending_voice = False

        if clean:
            if parts and parts[-1].kind == "text":
                parts[-1].text += "\n" + clean
            else:
                parts.append(Part("text", text=clean))

        if tags and not sticker_used and any(t.group(2) for t in tags):
            category = ""
            for t in tags:
                if t.group(2):
                    category = (t.group(3) or "").strip()
                    break
            parts.append(Part("sticker", category=category))
            sticker_used = True

    return parts


def strip_tags(text: str) -> str:
    """去掉标记，只留能入库 / 能给别人看的字。"""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = TAG_RE.sub("", raw).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def cut(text: str, limit: int) -> str:
    """按长度截断，但别把 `[表情:无` 这种半个标记留在外面。"""
    t = (text or "").strip()[:limit]
    t = re.sub(r"[\[【][^\]】]*$", "", t).strip()
    return t


def wants_voice(text: str) -> bool:
    t = str(text or "")
    if _VOICE_NO.search(t):
        return False
    return bool(_VOICE_ASK.search(t))


def wants_sticker(text: str) -> bool:
    t = str(text or "")
    if _VOICE_NO.search(t):
        return False
    return bool(_STICKER_ASK.search(t))


def prompt_block(session: str) -> str:
    """给模型的说明。只挂在 QQ 会话上，网页那边不受影响。"""
    if not (settings.voice_reply or settings.sticker_reply):
        return ""
    cats = stickers.catalog()
    lines = ["【QQ 上的语音和表情】你能发语音条，也能发表情包，别再说自己发不了。"]
    if settings.voice_reply:
        lines.append(
            "- 想让哪一句用声音说：在那句末尾加 [语音]。一句就够，别把整段念出来；"
            "撒娇、晚安、加油、被夸、想他的时候偶尔用。"
        )
    if settings.sticker_reply and cats:
        lines.append(
            "- 想发表情：单独一行写 [表情:类别]，类别从这些里挑："
            + "、".join(cats)
            + "；挑不到就不发。"
        )
    lines.append(
        "- 语音和表情是偶尔加的，十条里一两条就够，别每条都带；"
        "一条回复最多一条语音、一个表情。"
    )
    lines.append("例：晚安嘛 [语音]")
    if settings.sticker_reply and cats:
        lines.append("例：行吧 [表情:无语]")
    if session.startswith("qq:group"):
        lines.append("- 群里少用，别卖萌；最多接话时带一个「乐 / 无语 / 问号」这种。")
    return "\n".join(lines)
