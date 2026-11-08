"""用 .env 里的 BREEZE_API_KEY 合成一句，先听听声音，不碰 QQ。

用法：
    python tools/check_breeze.py
    python tools/check_breeze.py "换一句试试"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR, settings
from app.voice import Voice

DEFAULT = "呜哇，被你发现啦！我在认真思考呢，才没有摸鱼。"


def main() -> None:
    if not settings.breeze_api_key:
        raise SystemExit(
            "还没有 BREEZE_API_KEY。去 https://breezeblue.ai/app/developer/api-keys "
            "建一个，填到 .env 的 BREEZE_API_KEY= 后面再跑。"
        )
    text = " ".join(sys.argv[1:]).strip() or DEFAULT
    voice = Voice(settings)
    result = voice._breeze(text)
    if not result:
        raise SystemExit("合成失败，看上面的报错（401 是 key 不对，402/429 多半是额度）。")
    audio, mime = result
    suffix = ".wav" if "wav" in mime else ".mp3"
    out = DATA_DIR / f"breeze-test{suffix}"
    out.write_bytes(audio)
    print(f"ok：{len(audio)} 字节，{len(text)} 个字（{mime}）")
    print(f"文件：{out}")
    print(f"音色：{settings.breeze_voice_id}")


if __name__ == "__main__":
    main()
