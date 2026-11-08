"""表情包：`stickers/<类别>/` 里的图片，没图就退回 QQ 自带小黄脸。

模型只会说「发个哪一类的表情」，这里负责把它变成一个真能发出去的东西：

- 先在 `stickers/` 下找同名（或名字沾边）的文件夹，从里面随机挑一张图，同一会话尽量不连着发同一张；
- 找不到图，就查下面的 `QQ_FACES`，发 QQ 自带的小黄脸；
- 都没有，就当没这个表情，不发。

`stickers/` 里的图随便放：png / jpg / gif / webp 都行，一个类别一个文件夹。
`tools/make_stickers.py` 会生成一套很简单的占位卡，不喜欢就删掉换成自己的。
"""
from __future__ import annotations

import random
import re
import threading
from pathlib import Path

from . import qq_faces
from .config import ROOT, settings

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 别名 -> 标准类别。模型说「乐」「笑死」「绷不住」都算「开心」。
STANDARD: dict[str, tuple[str, ...]] = {
    "开心": ("乐", "高兴", "笑", "大笑", "笑死", "嘻嘻", "哈哈", "绷不住"),
    "无语": ("汗", "流汗", "沉默", "呃", "无奈"),
    "生气": ("哼", "怒", "气", "恼", "火大"),
    "委屈": ("难过", "可怜", "呜呜", "想哭"),
    "害羞": ("羞", "脸红", "不好意思", "腼腆"),
    "加油": ("冲", "棒", "好棒", "鼓励", "打气"),
    "晚安": ("睡", "困", "睡觉", "晚安啦"),
    "贴贴": ("抱", "拥抱", "亲亲", "贴", "抱抱"),
    "吃瓜": ("瓜", "看戏", "围观", "吃瓜群众"),
    "问号": ("疑问", "疑惑", "问", "?", "？"),
    "赞": ("点赞", "好", "牛", "强", "厉害"),
    "哭了": ("哭", "流泪", "呜", "大哭"),
    "急了": ("急", "破防", "慌", "急眼"),
    "绷不住": ("忍不住", "笑不活", "蚌埠住"),
}

# 标准类别 / 别名 -> QQ 自带表情 id
QQ_FACES: dict[str, int] = {
    "开心": 13, "乐": 13, "高兴": 13, "笑": 13, "大笑": 13, "笑死": 178,
    "无语": 27, "汗": 27, "流汗": 27, "沉默": 27, "呃": 179, "无奈": 27,
    "生气": 11, "哼": 11, "怒": 11, "气": 11,
    "委屈": 106, "难过": 15, "可怜": 111, "呜呜": 5, "想哭": 107,
    "害羞": 6, "羞": 6, "脸红": 6,
    "加油": 180, "冲": 180, "棒": 76, "好棒": 180,
    "晚安": 8, "睡": 8, "困": 25,
    "贴贴": 49, "抱": 49, "拥抱": 49, "亲亲": 109,
    "吃瓜": 190, "瓜": 190, "看戏": 190, "围观": 190,
    "问号": 32, "疑问": 32, "疑惑": 32, "?": 32, "？": 32,
    "赞": 76, "点赞": 76, "牛": 76, "强": 76, "厉害": 76,
    "哭了": 5, "哭": 5, "流泪": 5, "大哭": 9,
    "急了": 27, "急": 27, "破防": 27, "慌": 27,
    "绷不住": 20, "忍不住": 20, "笑不活": 20,
}

_lock = threading.RLock()
_last: dict[str, str] = {}


def root() -> Path:
    raw = (settings.sticker_dir or "").strip()
    return Path(raw).expanduser() if raw else (ROOT / "stickers")


def _clean(name: str) -> str:
    return re.sub(r"[\s\[】\]【:：,，。.!！?？]+", "", str(name or "")).strip()


def categories() -> list[str]:
    """有图的类别。没有 `stickers/` 或都是空文件夹就返回空。"""
    base = root()
    if not base.is_dir():
        return []
    out: list[str] = []
    try:
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            if any(f.is_file() and f.suffix.lower() in IMAGE_EXT for f in folder.iterdir()):
                out.append(folder.name)
    except OSError:
        return []
    return out


def _images(category: str) -> list[Path]:
    folder = root() / category
    try:
        return [f for f in sorted(folder.iterdir())
                if f.is_file() and f.suffix.lower() in IMAGE_EXT]
    except OSError:
        return []


def _standard(name: str) -> str:
    """把「笑死」「蚌埠住」这类说法归到标准类别。"""
    c = _clean(name)
    if not c:
        return ""
    if c in STANDARD:
        return c
    for canon, aliases in STANDARD.items():
        if c in aliases:
            return canon
    # 「有点无语」「开心一点」这种带修饰的，看包含关系（短的优先，免得「哭」吃掉「大哭」）
    for canon in sorted(list(STANDARD) + list(QQ_FACES), key=len, reverse=True):
        if canon in c:
            return canon
    return c


def _match_folder(name: str) -> str | None:
    """优先同名文件夹；其次名字互相包含；最后按标准类别找。"""
    c = _clean(name)
    cats = categories()
    if not cats:
        return None
    for cat in cats:
        if cat == c:
            return cat
    for cat in cats:
        if c and (c in cat or cat in c):
            return cat
    std = _standard(c)
    for cat in cats:
        if cat == std:
            return cat
    for cat in cats:
        if std and (std in cat or cat in std):
            return cat
    return None


def face_for(name: str) -> int | None:
    c = _clean(name)
    if not c:
        return None
    if c in QQ_FACES:
        return QQ_FACES[c]
    std = _standard(c)
    if std in QQ_FACES:
        return QQ_FACES[std]
    for key in sorted(QQ_FACES, key=len, reverse=True):
        if key in c:
            return QQ_FACES[key]
    return None


def pick_image(category: str, session: str = "") -> Path | None:
    c = _clean(category)
    # QQ 收藏的优先级高于 stickers/ 里的同名占位卡：那些是她自己的图
    if settings.qq_faces:
        wanted = c if c else ""
        favs = qq_faces.images(wanted)
        if not favs and c:
            favs = qq_faces.images(_standard(c))
        if favs:
            return _choose(favs, session)

    if c:
        folder = _match_folder(c)
    else:
        cats = categories()
        folder = random.choice(cats) if cats else None
    if not folder:
        return None
    files = _images(folder)
    if not files:
        return None
    return _choose(files, session)


def _choose(files: list[Path], session: str) -> Path:
    with _lock:
        last = _last.get(session)
        pool = [f for f in files if str(f) != last] or files
        chosen = random.choice(pool)
        _last[session] = str(chosen)
    return chosen


def resolve(category: str, session: str = "") -> tuple[str, Path | int] | None:
    """返回 ("image", Path) 或 ("face", id)；都没有就 None。"""
    image = pick_image(category, session)
    if image is not None:
        return "image", image
    if not _clean(category):
        # 没指定类别（比如主人只说「来个表情」）：随便挑一个小黄脸
        return "face", random.choice(list(QQ_FACES.values()))
    fid = face_for(category)
    if fid is not None:
        return "face", fid
    return None


def catalog(limit: int = 24) -> list[str]:
    """给模型挑的类别清单：QQ 收藏的标签优先，再是本地的，最后补几个自带表情。"""
    out: list[str] = []
    if settings.qq_faces:
        out.extend(qq_faces.tags())
        if qq_faces.count():
            out.append("QQ收藏")
    for name in categories():
        if name not in out:
            out.append(name)
    for name in ("开心", "无语", "生气", "委屈", "害羞", "加油", "晚安",
                 "贴贴", "吃瓜", "问号", "赞", "哭了", "急了", "绷不住"):
        if name not in out:
            out.append(name)
    return out[:limit]
