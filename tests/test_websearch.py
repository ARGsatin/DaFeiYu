"""上网看一眼：搜索词清洗、新闻判定、RSS 解析、结果相关性。"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from app import websearch as w

NEWS_XML = b"""<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>\xe5\xbf\x85\xe5\xba\x94</title>
<item>
  <title>LPL\xe8\xbd\xac\xe4\xbc\x9a\xe6\x9c\x9f\xef\xbc\x9aLeave\xe6\x88\x90\xe8\x87\xaa\xe7\x94\xb1\xe4\xba\xba</title>
  <link>https://example.com/a</link>
  <description>&lt;b&gt;26&lt;/b&gt;\xe8\xb5\x9b\xe5\xad\xa3</description>
  <pubDate>\xe5\x91\xa8\xe4\xba\x94, 30 10\xe6\x9c\x88 2026 05:17:00 GMT</pubDate>
</item>
</channel></rss>"""


class QueryTest(unittest.TestCase):
    def test_clean_question_drops_filler(self):
        self.assertEqual(w.clean_question("帮我查一下最近有什么转会的消息"), "转会的消息")
        self.assertEqual(w.clean_question("今天有LPL比赛吗？"), "有LPL比赛吗")

    def test_drop_time_words(self):
        self.assertEqual(w.drop_time_words("最近 转会 消息 英雄联盟 LPL"), "转会 消息 英雄联盟 LPL")
        self.assertEqual(w.drop_time_words("今天"), "")

    def test_wants_news(self):
        for q in ["今天谁打", "最近有什么转会", "最新版本", "BLG赢了没"]:
            self.assertTrue(w.wants_news(q), q)
        for q in ["索尼A7C2怎么样", "帮我写个周报", "你叫什么"]:
            self.assertFalse(w.wants_news(q), q)

    def test_anchors(self):
        lt = time.struct_time((2026, 11, 2, 17, 0, 0, 0, 306, 0))
        self.assertEqual(w.time_anchor(lt), "2026年11月2日")
        self.assertEqual(w.month_anchor(lt), "2026年11月")


class DateTest(unittest.TestCase):
    def test_chinese_pubdate(self):
        dt = w._parse_pubdate("周五, 30 10月 2026 05:17:00 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 10, 30))

    def test_english_pubdate(self):
        dt = w._parse_pubdate("Mon, 19 Oct 2026 14:31:00 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.month, dt.day), (10, 19))

    def test_garbage_pubdate(self):
        self.assertIsNone(w._parse_pubdate(""))
        self.assertIsNone(w._parse_pubdate("昨天"))

    def test_when(self):
        date, days = w._when("周五, 30 10月 2026 05:17:00 GMT")
        self.assertEqual(date, "10-30")
        self.assertGreaterEqual(days, 0)


class RssTest(unittest.TestCase):
    def test_parse_rss(self):
        items = w.parse_rss(NEWS_XML, "新闻")
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertIn("LPL", it["title"])
        self.assertEqual(it["url"], "https://example.com/a")
        self.assertNotIn("<b>", it["snippet"])  # 标签去干净
        self.assertEqual(it["date"], "10-30")
        self.assertEqual(it["source"], "新闻")

    def test_parse_broken_rss(self):
        self.assertEqual(w.parse_rss(b"not xml", "新闻"), [])


class RelevanceTest(unittest.TestCase):
    def test_relevant(self):
        item = {"title": "LPL转会期：Leave成自由人", "snippet": "转会市场升温"}
        self.assertTrue(w.relevant("LOL 转会 LPL", item))

    def test_irrelevant(self):
        item = {"title": "英雄联盟官方网站", "snippet": "海量英雄"}
        self.assertFalse(w.relevant("LOL 转会 LPL", item))


class DigestTest(unittest.TestCase):
    def test_dates_only_for_news(self):
        items = [
            {"title": "转会新闻", "url": "https://a.com/x", "snippet": "s",
             "date": "10-22", "days": 3, "source": "新闻"},
            {"title": "参数页", "url": "https://b.com/y", "snippet": "s",
             "date": "11-02", "days": 0, "source": "网页"},
        ]
        text = w.format_digest(items)
        self.assertIn("- 10-22 转会新闻", text)
        self.assertIn("- 参数页", text)
        self.assertNotIn("11-02 参数页", text)  # 网页的日期是抓取时间，不标
        self.assertIn("a.com", text)

    def test_digest_filters_unrelated(self):
        fake = [
            {"title": "LPL转会期爆料", "url": "https://a.com/1", "snippet": "转会",
             "date": "10-22", "days": 3, "source": "新闻"},
            {"title": "世界赛最差开局", "url": "https://a.com/2", "snippet": "瑞士轮",
             "date": "10-24", "days": 1, "source": "新闻"},
        ]
        with mock.patch.object(w, "search", return_value=fake):
            text = w.digest("LOL 转会 LPL", question="最近有什么转会")
        self.assertIn("LPL转会期爆料", text)
        self.assertNotIn("世界赛最差开局", text)

    def test_empty_digest(self):
        with mock.patch.object(w, "search", return_value=[]):
            self.assertEqual(w.digest("LOL 转会", question="最近转会"), "")


if __name__ == "__main__":
    unittest.main()
