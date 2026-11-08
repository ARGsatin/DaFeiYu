"""生成一套很简单的表情卡，放在 `stickers/<类别>/` 下。

只是占位：想换就把它删了，往同名文件夹里丢自己的图（png / jpg / gif 都行）。
每张卡是「一个大表情 + 一句大白话」，QQ 里缩成小图也看得清。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "stickers"

# 类别, 卡上的字, 表情, 背景色, 描边色
CARDS = [
    ("开心", "乐", "😂", (255, 243, 196), (242, 201, 76)),
    ("无语", "无语", "😑", (232, 238, 247), (160, 174, 192)),
    ("生气", "哼", "😤", (255, 224, 224), (235, 140, 140)),
    ("委屈", "委屈", "🥺", (243, 232, 255), (186, 156, 224)),
    ("害羞", "害羞", "😳", (255, 228, 236), (240, 160, 185)),
    ("加油", "加油", "💪", (227, 248, 232), (120, 200, 150)),
    ("晚安", "晚安", "😴", (230, 230, 250), (150, 150, 205)),
    ("贴贴", "贴贴", "🤗", (255, 239, 230), (240, 175, 130)),
    ("吃瓜", "吃瓜", "🍉", (233, 251, 231), (130, 200, 130)),
    ("问号", "？", "❓", (238, 241, 245), (150, 160, 175)),
    ("赞", "赞", "👍", (255, 247, 219), (230, 190, 90)),
    ("哭了", "呜呜", "😭", (227, 242, 253), (130, 180, 230)),
    ("急了", "急了", "😅", (255, 236, 220), (235, 165, 110)),
    ("绷不住", "绷不住", "🤣", (255, 240, 210), (240, 195, 100)),
]

SIZE = 360


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    try:
        return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001
        return None


def card(text: str, emoji: str, bg: tuple, edge: tuple, out: Path,
         emoji_font_path: str, text_font_path: str) -> bool:
    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=40,
                        outline=(255, 255, 255, 210), width=8)
    d.rounded_rectangle((17, 17, SIZE - 17, SIZE - 17), radius=32,
                        outline=edge + (110,), width=3)
    d.ellipse((SIZE / 2 - 92, 34, SIZE / 2 + 92, 218), fill=(255, 255, 255, 190))

    emoji_font = _font(emoji_font_path, 150)
    if emoji_font:
        try:
            d.text((SIZE / 2, 126), emoji, font=emoji_font,
                   anchor="mm", embedded_color=True)
        except Exception:  # noqa: BLE001
            pass

    text_font = _font(text_font_path, 58 if len(text) <= 2 else 48)
    if text_font:
        d.text((SIZE / 2, 280), text, font=text_font, anchor="mm", fill=(52, 48, 45))

    # 左下角一点点水印，不抢字
    tiny = _font(text_font_path, 17)
    if tiny:
        d.text((26, 334), "鲸鲸", font=tiny, fill=(120, 110, 100, 150),
               anchor="ls")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return True


def main() -> None:
    emoji_font = r"C:\Windows\Fonts\seguiemj.ttf"
    text_font = r"C:\Windows\Fonts\msyhbd.ttc"
    if not Path(text_font).exists():
        text_font = r"C:\Windows\Fonts\msyh.ttc"
    made = 0
    for i, (category, text, emoji, bg, edge) in enumerate(CARDS):
        out = OUT / category / f"{i + 1:02d}.png"
        if card(text, emoji, bg, edge, out, emoji_font, text_font):
            made += 1
            print(f"[sticker] {out.relative_to(ROOT)}")
    print(f"共 {made} 张。想换成自己的图，直接往 stickers/<类别>/ 里丢就行。")


if __name__ == "__main__":
    main()
