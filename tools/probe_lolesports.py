"""从 lolesports.com 页面里把嵌入的赛事 JSON 抠出来。"""
import json
import re

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def extract_arrays(html: str, key: str = "events") -> list[list]:
    out = []
    needle = f'"{key}":['
    start = 0
    while True:
        i = html.find(needle, start)
        if i < 0:
            break
        j = i + len(needle) - 1  # 指向 '['
        depth, k, in_str, esc = 0, j, False, False
        while k < len(html):
            ch = html[k]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        if depth == 0 and k < len(html):
            try:
                out.append(json.loads(html[j : k + 1]))
            except Exception as exc:
                print("parse fail", exc)
        start = i + 1
    return out


html = requests.get("https://lolesports.com/schedule", headers={"User-Agent": UA}, timeout=30).text
arrays = extract_arrays(html, "events")
print("arrays:", len(arrays), "sizes:", [len(a) for a in arrays][:5])

events = max(arrays, key=len) if arrays else []
print("events:", len(events))
if events:
    print(json.dumps(events[0], ensure_ascii=False)[:1500])
