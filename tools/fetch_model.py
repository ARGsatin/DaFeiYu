"""从 ModelScope 拉一个 Qwen 模型到本地 models/ 目录（GitHub 走不通时用这个）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

REPO = sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen2.5-1.5B-Instruct"
NAME = REPO.split("/")[-1]
DEST = Path(__file__).resolve().parent.parent / "models" / NAME
BASE = f"https://modelscope.cn/models/{REPO}/resolve/master/"

FILES = [
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
]


def download(name: str) -> None:
    target = DEST / name
    if target.exists() and target.stat().st_size > 0:
        print(f"[skip] {name} ({target.stat().st_size/1e6:.1f} MB)", flush=True)
        return
    url = BASE + name
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        tmp = target.with_suffix(target.suffix + ".part")
        done = 0
        t0 = time.time()
        last = 0.0
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 5:
                    last = now
                    speed = done / max(1e-6, now - t0) / 1e6
                    pct = (done / total * 100) if total else 0
                    print(f"  {name}: {done/1e6:.0f}/{total/1e6:.0f} MB ({pct:.0f}%) {speed:.1f} MB/s", flush=True)
        tmp.replace(target)
        print(f"[done] {name} {target.stat().st_size/1e6:.1f} MB", flush=True)


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"目标目录：{DEST}", flush=True)
    for f in FILES:
        for attempt in range(3):
            try:
                download(f)
                break
            except Exception as exc:
                print(f"[retry {attempt+1}] {f}: {exc}", flush=True)
                time.sleep(3)
        else:
            raise SystemExit(f"下载失败：{f}")
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
