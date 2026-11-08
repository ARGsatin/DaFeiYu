"""语音 / 表情标记的解析和兜底规则。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import delivery, emote, stickers
from app.adapters import qq


class ParseTests(unittest.TestCase):
    def test_plain_text(self):
        parts = emote.parse("今天好累呀\n那你歇会儿")
        self.assertEqual([p.kind for p in parts], ["text"])
        self.assertEqual(parts[0].text, "今天好累呀\n那你歇会儿")

    def test_voice_tag_at_end_of_line(self):
        parts = emote.parse("晚安嘛 [语音]")
        self.assertEqual([p.kind for p in parts], ["voice"])
        self.assertEqual(parts[0].text, "晚安嘛")

    def test_voice_tag_on_its_own_line_after_sentence(self):
        parts = emote.parse("晚安嘛\n[语音]")
        self.assertEqual([p.kind for p in parts], ["voice"])
        self.assertEqual(parts[0].text, "晚安嘛")

    def test_voice_tag_on_its_own_line_before_sentence(self):
        parts = emote.parse("[语音]\n晚安嘛")
        self.assertEqual([p.kind for p in parts], ["voice"])
        self.assertEqual(parts[0].text, "晚安嘛")

    def test_long_sentence_stays_text(self):
        long = "这句话真的很长" * 10
        parts = emote.parse(f"{long} [语音]")
        self.assertEqual([p.kind for p in parts], ["text"])
        self.assertEqual(parts[0].text, long)

    def test_sticker_tag(self):
        parts = emote.parse("行吧 [表情:无语]")
        self.assertEqual([p.kind for p in parts], ["text", "sticker"])
        self.assertEqual(parts[1].category, "无语")

    def test_sticker_only(self):
        parts = emote.parse("[表情: 乐]")
        self.assertEqual([p.kind for p in parts], ["sticker"])
        self.assertEqual(parts[0].category, "乐")

    def test_only_one_voice_and_one_sticker(self):
        parts = emote.parse("第一句 [语音]\n第二句 [语音]\n[表情:乐]\n[表情:无语]")
        self.assertEqual([p.kind for p in parts], ["voice", "text", "sticker"])

    def test_cut_keeps_tags_whole(self):
        self.assertEqual(emote.cut("晚安嘛 [语音]", 120), "晚安嘛 [语音]")
        cut = emote.cut("一二三四五[表情:无", 8)
        self.assertNotIn("[", cut)

    def test_strip_tags(self):
        self.assertEqual(emote.strip_tags("晚安嘛 [语音]\n[表情:无语]"), "晚安嘛")

    def test_unknown_brackets_survive(self):
        parts = emote.parse("（尾巴摇摇）好呀")
        self.assertEqual(parts[0].text, "（尾巴摇摇）好呀")


class RequestTests(unittest.TestCase):
    def test_voice_requests(self):
        for text in ("能发个加油的语音吗", "发条语音嘛", "你会唱歌吗", "说句话听听"):
            self.assertTrue(emote.wants_voice(text), text)

    def test_not_voice_requests(self):
        for text in ("别发语音", "语音发不了", "今天好累", "你说话真逗"):
            self.assertFalse(emote.wants_voice(text), text)

    def test_sticker_requests(self):
        for text in ("来个表情包", "斗图吗", "发个表情"):
            self.assertTrue(emote.wants_sticker(text), text)

    def test_not_sticker_requests(self):
        for text in ("别发表情", "今天好累"):
            self.assertFalse(emote.wants_sticker(text), text)


class StickerTests(unittest.TestCase):
    def test_builtin_faces(self):
        self.assertEqual(stickers.face_for("无语"), stickers.QQ_FACES["无语"])
        self.assertEqual(stickers.face_for("乐"), stickers.QQ_FACES["乐"])
        self.assertIsNotNone(stickers.face_for("绷不住"))

    def test_local_folder_and_pick(self):
        old = stickers.settings.sticker_dir
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "猫猫"
            folder.mkdir()
            (folder / "a.png").write_bytes(b"png")
            (folder / "b.png").write_bytes(b"png")
            stickers.settings.sticker_dir = tmp
            try:
                self.assertIn("猫猫", stickers.categories())
                picked = stickers.pick_image("猫猫", session="s1")
                self.assertIsNotNone(picked)
                self.assertEqual(picked.parent.name, "猫猫")
                self.assertEqual(stickers.resolve("猫猫", "s1")[0], "image")
                # 没有的类别退回小黄脸
                self.assertEqual(stickers.resolve("无语", "s1")[0], "face")
            finally:
                stickers.settings.sticker_dir = old

    def test_catalog_has_choices(self):
        self.assertGreaterEqual(len(stickers.catalog()), 5)

    def test_empty_category_still_picks_something(self):
        self.assertIsNotNone(stickers.resolve("", "s2"))
        self.assertIsNone(stickers.resolve("zzz", "s2"))


class QQPayloadTests(unittest.TestCase):
    def test_segments_go_to_the_right_endpoint(self):
        calls = []
        old = qq._post
        qq._post = lambda method, payload, timeout=20: calls.append((method, payload)) or {"status": "ok"}
        try:
            qq.send_segment({"type": "private", "user_id": 1},
                            {"type": "face", "data": {"id": "14"}})
            qq.send_segment({"type": "group", "group_id": 2},
                            {"type": "record", "data": {"file": "x"}})
        finally:
            qq._post = old
        self.assertEqual(calls[0][0], "send_private_msg")
        self.assertEqual(calls[0][1]["message"][0]["type"], "face")
        self.assertEqual(calls[1][0], "send_group_msg")
        self.assertEqual(calls[1][1]["group_id"], 2)


class LimitTests(unittest.TestCase):
    def test_explicit_request_bypasses_gap_but_not_daily_cap(self):
        old_file = delivery.STATE_FILE
        old_max, old_gap = delivery.settings.voice_max, delivery.settings.voice_gap
        with tempfile.TemporaryDirectory() as tmp:
            delivery.STATE_FILE = Path(tmp) / "emote.json"
            delivery.settings.voice_max = 2
            delivery.settings.voice_gap = 9999
            try:
                self.assertTrue(delivery._allowed("qq:private:1", "voice", explicit=False))
                delivery._note("qq:private:1", "voice")
                # 间隔没到，她自己想发就不行；主人点名要可以
                self.assertFalse(delivery._allowed("qq:private:1", "voice", explicit=False))
                self.assertTrue(delivery._allowed("qq:private:1", "voice", explicit=True))
                delivery._note("qq:private:1", "voice")
                # 每天两条封顶，点名也不行
                self.assertFalse(delivery._allowed("qq:private:1", "voice", explicit=True))
            finally:
                delivery.STATE_FILE = old_file
                delivery.settings.voice_max = old_max
                delivery.settings.voice_gap = old_gap

    def test_group_limits_are_stricter(self):
        self.assertLessEqual(delivery._limits("qq:group:1")["voice_max"],
                             delivery._limits("qq:private:1")["voice_max"])


if __name__ == "__main__":
    unittest.main()
