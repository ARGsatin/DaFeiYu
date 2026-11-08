"""给鱼装个「上网看一眼」的能力。

以前只扒 `cn.bing.com` 的网页 HTML，问题有两个：
一是必应一改版就抓瞎；二是网页结果没有日期、SEO 页面翻来覆去就那三个官网，
问「今天谁打」「最近有什么转会」时，翻出来的全是「英雄」「报价」这种不相干的东西。

现在分两条路，都走必应的 RSS（结构稳定、每条带发布时间）：

- **新闻**（问「今天 / 最近 / 转会 / 官宣 / 赛程」）：`bing.com/news/search?format=rss`，
  结果新、带日期，中文电竞新闻基本都在；
- **普通网页**：`cn.bing.com/search?format=rss`，实在不行再退回扒 HTML。

喂给模型时把日期一起带上，并说清楚「问到今天/现在而没有今天的结果，就说没看到」，
免得她拿三天前的新闻冒充刚发生的。
"""
from __future__ import annotations

import html as html_lib
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
CACHE_TTL = 300
_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

# 问句里这些词说明要的是「新鲜的」消息，优先走新闻
NEWSY = re.compile(
    r"今天|今日|昨天|昨日|前天|今晚|刚刚|刚才|现在|最近|最新|这几天|这两天|本周|这周"
    r"|新闻|消息|转会|官宣|签约|离队|加入|加盟|开赛|赛程|比分|赛果|谁赢|赢了|输了|战绩|排名|积分"
    r"|版本|补丁|更新|发布|活动"
)
# 改写搜索词前先去掉这些口水话，剩下的才是「要找什么」
FILLER = re.compile(
    r"帮我|帮忙|麻烦你?|你能不能?|可不可以?|能不能|可以帮我|给我|替我"
    r"|查一下|查查|搜一下|搜搜|看一下|看看|瞧一瞧|问一下|问问"
    r"|有没有|有啥|有什么|有哪些|还有|都是|都|是些|什么消息|啥消息"
    r"|我想知道|我想问|请问|告诉?我|说说|讲讲|介绍一下"
)
TIME_WORD = re.compile(
    r"今天|今日|今晚|现在|最近|最新|这几天|这两天|本周|这周|昨天|昨日|前天|刚刚|刚才"
)
BAD_URL = re.compile(r"^(javascript:|#|about:)")


# ---------------- 小工具 ----------------
def drop_time_words(text: str) -> str:
    """搜索词里不要「最近 / 今天」：必应新闻带上这类词经常一条都不给，时间交给排序和时间锚。"""
    return re.sub(r"\s+", " ", TIME_WORD.sub(" ", str(text or ""))).strip()


def clean_question(text: str) -> str:
    """去掉口水话、时间词和标点，留下「要找什么」。"""
    t = str(text or "").strip()
    t = FILLER.sub(" ", t)
    t = drop_time_words(t)
    t = re.sub(r"[，。！？、,.!?；;：:（）()「」“”\"]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def wants_news(text: str) -> bool:
    return bool(NEWSY.search(str(text or "")))


def time_anchor(now: time.struct_time | None = None) -> str:
    """问「今天」的时候，把具体日期塞进搜索词，比光问「今天」准。"""
    lt = now or time.localtime()
    return f"{lt.tm_year}年{lt.tm_mon}月{lt.tm_mday}日"


def month_anchor(now: time.struct_time | None = None) -> str:
    lt = now or time.localtime()
    return f"{lt.tm_year}年{lt.tm_mon}月"


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").replace("www.", "")
    except Exception:
        return ""


def _parse_pubdate(pub: str) -> datetime | None:
    """必应中文站给的是「周五, 30 10月 2026 05:17:00 GMT」，英文星期和月份解析不了，得自己认。"""
    pub = (pub or "").strip()
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    m = re.search(r"(\d{1,2})\s+(\d{1,2})月\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", pub)
    if m:
        day, month, year, hour, minute, second = m.groups()
        try:
            return datetime(
                int(year), int(month), int(day), int(hour), int(minute),
                int(second or 0), tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    return None


def _when(pub: str, now: time.struct_time | None = None) -> tuple[str, int]:
    """RSS 的发布时间 → (11-02, 距今几天)。解析不了就给 ('', 999)。"""
    dt = _parse_pubdate(pub)
    if dt is None:
        return "", 999
    days = int((time.time() - dt.timestamp()) // 86400)
    return f"{dt.month:02d}-{dt.day:02d}", max(0, days)


# ---------------- 取结果 ----------------
def _get(url: str, params: dict | None = None, timeout: int = 15) -> requests.Response:
    return requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)


def parse_rss(raw: bytes, source: str, now: time.struct_time | None = None) -> list[dict]:
    """把必应的 RSS 拆成 [{title, url, snippet, date, days, source}]。"""
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    out: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url or BAD_URL.match(url):
            continue
        snippet = re.sub(r"<[^>]+>", "", item.findtext("description") or "")
        snippet = html_lib.unescape(re.sub(r"\s+", " ", snippet)).strip()
        date, days = _when((item.findtext("pubDate") or "").strip(), now)
        out.append({
            "title": html_lib.unescape(title),
            "url": url,
            "snippet": snippet,
            "date": date,
            "days": days,
            "source": source,
        })
    return out


def _bing_news(query: str, n: int) -> list[dict]:
    # 注意：加上 setmkt 参数必应会返回 HTML 而不是 RSS，所以直接把 query 拼进 URL
    url = "https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote(query)
    r = _get(url)
    r.raise_for_status()
    return parse_rss(r.content, "新闻")[:n]


def _bing_rss(query: str, n: int) -> list[dict]:
    r = _get("https://cn.bing.com/search", {"q": query, "format": "rss"})
    r.raise_for_status()
    return parse_rss(r.content, "网页")[:n]


def _bing_html(query: str, n: int) -> list[dict]:
    """老路子，留着兜底：RSS 拿不到时才用。"""
    r = _get("https://cn.bing.com/search", {"q": query})
    r.encoding = "utf-8"
    out: list[dict] = []
    for item in re.findall(r'<li class="b_algo".*?</li>', r.text, flags=re.S)[:n]:
        m = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item, flags=re.S)
        if not m:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", item, flags=re.S)
        out.append({
            "title": html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(),
            "url": m.group(1),
            "snippet": re.sub(r"<[^>]+>", "", p.group(1)).strip() if p else "",
            "date": "",
            "days": 999,
            "source": "网页",
        })
    return out


def search_news(query: str, n: int = 5) -> list[dict]:
    """新闻：宁可少几条，也不拿几个月前的充数。"""
    items = _cached("news", query, lambda: _bing_news(query, n + 3))
    fresh = [it for it in items if it["days"] <= 30]
    return sorted(fresh, key=lambda it: it["days"])[:n]


def search_web(query: str, n: int = 5) -> list[dict]:
    """普通网页：先 RSS，空了再扒 HTML。"""
    items = _cached("web", query, lambda: _bing_rss(query, n))
    if not items:
        items = _cached("html", query, lambda: _bing_html(query, n))
    return items[:n]


def _cached(kind: str, query: str, fetch) -> list[dict]:
    key = (kind, query.strip())
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        items = fetch()
    except Exception as exc:  # noqa: BLE001
        print(f"[search] {kind} 查询失败：{exc}")
        items = []
    _cache[key] = (now, items)
    return items


def search(query: str, n: int = 5, *, news: bool | None = None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    if news is None:
        news = wants_news(query)
    if news:
        items = search_news(query, n)
        if items:
            return items
    return search_web(query, n)


# ---------------- 拼给模型看 ----------------
CJK = re.compile(r"[\u4e00-\u9fff]+")


def _grams(text: str) -> set[str]:
    out: set[str] = set()
    for run in CJK.findall(str(text or "")):
        out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i : i + 2])
    return out


def relevant(query: str, item: dict, need: float = 0.2, must: list[str] | None = None) -> bool:
    """搜出来的东西跟问题沾不沾边。

    must 是「必须出现的词」：电竞群里的搜索必须提到 LPL/英雄联盟，
    不然问转会会被英超、NBA 的转会消息顶掉。
    """
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    if must and not any(m.lower() in text for m in must if m):
        return False
    q = _grams(query)
    if not q:
        return True
    return len(q & _grams(text)) / float(len(q)) >= need


def format_digest(items: list[dict], now: time.struct_time | None = None) -> str:
    """带日期的一小段。日期是给模型「别拿旧的当新的」用的。"""
    if not items:
        return ""
    lt = now or time.localtime()
    today = f"{lt.tm_mon:02d}-{lt.tm_mday:02d}"
    lines = []
    for it in items:
        snippet = re.sub(r"\s+", " ", it.get("snippet") or "").strip()
        if len(snippet) > 140:
            snippet = snippet[:140] + "…"
        # 只有新闻的日期是「发布时间」；网页那条日期是必应的抓取时间，标出来会误导
        stamp = (it.get("date") or "") if it.get("source") == "新闻" else ""
        head = f"- {stamp} " if stamp else "- "
        host = _host(it.get("url") or "")
        tail = f"（{host}）" if host else ""
        lines.append(f"{head}{it['title']}：{snippet}{tail}")
    return (
        f"【刚从网上搜到的（今天是 {today}，前面是发布时间；可能不全，但都是真新闻）】\n"
        + "\n".join(lines)
        + "\n（下面有跟这个问题相关的消息，就直接拿出来说，别再说「没查到 / 没这消息」；"
        "只是别改日期、别补这里没写的事实。问到「今天/现在」而这里没有今天的，就说「今天的还没看到」，别编。）"
    )


def digest(query: str, n: int = 5, *, hint: str = "", question: str = "") -> str:
    """搜一次，压成一小段塞进提示词。question 用来判断要不要走新闻。"""
    query = (query or "").strip()
    if not query:
        return ""
    news = wants_news(question or query)

    # 限定词先并进主查询：它既影响搜什么，也决定哪些结果算「沾边」
    must = [t for t in (hint or "").split() if t]
    if hint and not any(t in query for t in must):
        query = f"{query} {hint}"

    def ok(item: dict) -> bool:
        return relevant(query, item, must=must)

    items = [it for it in search(query, n, news=news) if ok(it)]
    # 不够就换个查法补：新闻退回问题原话，网页加时间限定（都还得过同一个相关性检查）
    if len(items) < min(3, n):
        variants: list[str] = []
        core = clean_question(question)
        if news and core:
            variants.append(f"{core} {hint}".strip())
        if not news:
            variants.append(f"{query} {month_anchor()}".strip())
        for variant in variants:
            if not variant or variant == query:
                continue
            seen = {it["url"] for it in items}
            for it in search(variant, n, news=news):
                if it["url"] not in seen and ok(it):
                    items.append(it)
                    seen.add(it["url"])
            if len(items) >= min(3, n):
                break

    if news:
        items.sort(key=lambda it: it["days"])
    return format_digest(items[:n])

    if news:
        items.sort(key=lambda it: it["days"])
    return format_digest(items[:n])
