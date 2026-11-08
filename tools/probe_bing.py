"""试探：必应搜索结果能不能解析。"""
import re
import urllib.parse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def bing(query: str, n: int = 5) -> list[dict]:
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.encoding = "utf-8"
    html = r.text

    items = re.findall(r'<li class="b_algo".*?</li>', html, flags=re.S)
    out = []
    for item in items[:n]:
        m = re.search(r"<h2[^>]*>.*?<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", item, flags=re.S)
        if not m:
            continue
        link = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        p = re.search(r"<p[^>]*>(.*?)</p>", item, flags=re.S)
        snippet = re.sub(r"<[^>]+>", "", p.group(1)).strip() if p else ""
        out.append({"title": title, "url": link, "snippet": snippet})
    return out


if __name__ == "__main__":
    for q in ["LPL 赛程 今天", "BLG 最近比赛结果"]:
        print("=" * 20, q)
        for it in bing(q):
            print(f"- {it['title']}\n  {it['snippet'][:120]}")
