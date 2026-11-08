"""生成大肥鱼的图标：assets/fish.ico（托盘 / exe）和 assets/fish.png（窗口）。

配色和网页里那个头像一套：身子 #a9f0ff → #2f93b8，尾巴 #5fc8e6。
画得糙没关系，16px 下能认出是条鱼就行。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

BODY_TOP = (169, 240, 255, 255)
BODY_BOTTOM = (47, 147, 184, 255)
TAIL = (95, 200, 230, 255)
FIN = (120, 215, 240, 255)
EYE = (4, 32, 43, 255)
SHINE = (234, 252, 255, 255)


def _fish(size: int) -> Image.Image:
    s = size
    # 先画大的再缩，边缘不生硬
    k = 4
    w = s * k
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(x: float, y: float) -> tuple[int, int]:
        return (int(x * w), int(y * w))

    # 尾巴（在身子后面）
    d.polygon([px(0.70, 0.50), px(0.97, 0.26), px(0.93, 0.50), px(0.97, 0.74)], fill=TAIL)
    # 背鳍
    d.polygon([px(0.34, 0.28), px(0.46, 0.13), px(0.55, 0.27)], fill=FIN)
    # 身子：上浅下深，用一条条横线做渐变
    left, top, right, bottom = 0.05, 0.22, 0.78, 0.79
    steps = max(64, w // 2)
    for i in range(steps):
        y0 = top + (bottom - top) * i / steps
        y1 = top + (bottom - top) * (i + 1) / steps
        t = i / max(1, steps - 1)
        color = tuple(
            int(BODY_TOP[c] + (BODY_BOTTOM[c] - BODY_TOP[c]) * t) for c in range(4)
        )
        cx, cy = (left + right) / 2 * w, (y0 + y1) / 2 * w
        rx = (right - left) / 2 * w
        ry = (bottom - top) / 2 * w
        # 用椭圆的一半宽度近似这一行的弦长
        dy = (cy - (top + bottom) / 2 * w) / ry
        chord = rx * (max(0.0, 1 - dy * dy) ** 0.5)
        d.rectangle([cx - chord, y0 * w, cx + chord, y1 * w + 1], fill=color)

    # 眼睛 + 高光
    d.ellipse([px(0.26, 0.40), px(0.36, 0.53)], fill=EYE)
    d.ellipse([px(0.28, 0.415), px(0.315, 0.455)], fill=SHINE)
    # 腮红
    d.ellipse([px(0.44, 0.55), px(0.53, 0.62)], fill=(255, 150, 180, 90))
    # 嘴
    d.arc([px(0.16, 0.60), px(0.34, 0.70)], 20, 150, fill=(10, 74, 94, 255), width=max(1, w // 64))

    return img.resize((s, s), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = _fish(256)
    big.save(OUT / "fish.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.save(OUT / "fish.ico", sizes=[(s, s) for s in sizes])
    print("wrote", OUT / "fish.png", "and", OUT / "fish.ico")


if __name__ == "__main__":
    main()
