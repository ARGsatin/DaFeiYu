import re

import requests

from app import websearch

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def text_of(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


for q in ["LPL 赛程", "LPL 今日赛程"]:
    print("=" * 40, q)
    for item in websearch.search(q, 4):
        url = item["url"]
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            r.encoding = r.apparent_encoding or "utf-8"
            t = text_of(r.text)
            hits = [k for k in ("BLG", "JDG", "TES", "LPL", "赛程") if k in t]
            print(f"  [{r.status_code}] len={len(t):>7} hits={hits[:4]}  {url[:70]}")
        except Exception as exc:
            print(f"  [ERR] {type(exc).__name__}  {url[:70]}")
