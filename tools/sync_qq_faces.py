"""立刻把 QQ 账号「收藏」里的表情同步到 data/qq-faces/，并看看有哪些标签。

用法：python tools/sync_qq_faces.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import qq_faces


def main() -> None:
    ok = qq_faces.sync(force=True)
    if not ok:
        raise SystemExit("同步失败：先确认 NapCat 在跑、QQ_API 对，以及账号已登录。")
    print(f"共 {qq_faces.count()} 张收藏表情")
    tags = qq_faces.tags()
    print("标签：" + ("、".join(tags) if tags else "（还没有标签，会归到「QQ收藏」随机发）"))
    print("目录：data/qq-faces/")


if __name__ == "__main__":
    main()
