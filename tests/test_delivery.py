"""发送顺序和失败兜底：不碰真 QQ，也不真的生成语音。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import delivery
from app.adapters import qq


class FakeVoice:
    def __init__(self, ok: bool = True):
        self.ok = ok

    def tts_sync(self, text: str):
        return (b"fake-mp3", "audio/mpeg") if self.ok else None


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls: list[tuple] = []
        self._old = {
            "STATE_FILE": delivery.STATE_FILE,
            "VOICE_DIR": delivery.VOICE_DIR,
            "shared_voice": delivery.shared_voice,
            "send": qq.send,
            "send_record": qq.send_record,
            "send_image": qq.send_image,
            "send_face": qq.send_face,
            "voice_max": delivery.settings.voice_max,
            "sticker_max": delivery.settings.sticker_max,
            "voice_gap": delivery.settings.voice_gap,
            "sticker_gap": delivery.settings.sticker_gap,
        }
        delivery.STATE_FILE = Path(self.tmp.name) / "emote.json"
        delivery.VOICE_DIR = Path(self.tmp.name) / "voice"
        delivery.shared_voice = lambda: FakeVoice()
        qq.send = lambda target, text: self.calls.append(("text", text)) or True
        qq.send_record = lambda target, path: self.calls.append(("record", Path(path).name)) or True
        qq.send_image = lambda target, path: self.calls.append(("image", Path(path).name)) or True
        qq.send_face = lambda target, fid: self.calls.append(("face", fid)) or True
        delivery.settings.voice_max = 10
        delivery.settings.sticker_max = 10
        delivery.settings.voice_gap = 0
        delivery.settings.sticker_gap = 0

    def tearDown(self):
        delivery.STATE_FILE = self._old["STATE_FILE"]
        delivery.VOICE_DIR = self._old["VOICE_DIR"]
        delivery.shared_voice = self._old["shared_voice"]
        qq.send = self._old["send"]
        qq.send_record = self._old["send_record"]
        qq.send_image = self._old["send_image"]
        qq.send_face = self._old["send_face"]
        delivery.settings.voice_max = self._old["voice_max"]
        delivery.settings.sticker_max = self._old["sticker_max"]
        delivery.settings.voice_gap = self._old["voice_gap"]
        delivery.settings.sticker_gap = self._old["sticker_gap"]

    def test_voice_then_sticker(self):
        result = delivery.send_qq({"type": "private", "user_id": 1},
                                  "qq:private:1", "晚安嘛 [语音]\n[表情:开心]")
        self.assertEqual([c[0] for c in self.calls], ["record", "image"])
        self.assertTrue(result["voice"])
        self.assertTrue(result["sticker"])

    def test_text_is_still_text(self):
        delivery.send_qq({"type": "private", "user_id": 1}, "qq:private:1",
                         "今天好累\n那你歇会儿")
        self.assertTrue(self.calls)
        self.assertTrue(all(c[0] == "text" for c in self.calls))

    def test_explicit_voice_request_without_tag(self):
        delivery.send_qq({"type": "private", "user_id": 1}, "qq:private:1",
                         "唔…饿了", user_text="发个语音嘛")
        self.assertEqual(self.calls[0][0], "record")

    def test_tts_failure_falls_back_to_text(self):
        delivery.shared_voice = lambda: FakeVoice(ok=False)
        result = delivery.send_qq({"type": "private", "user_id": 1}, "qq:private:1",
                                  "晚安嘛 [语音]")
        self.assertEqual(self.calls[0][0], "text")
        self.assertFalse(result["voice"])

    def test_record_failure_falls_back_to_text(self):
        qq.send_record = lambda target, path: False
        delivery.send_qq({"type": "private", "user_id": 1}, "qq:private:1",
                         "晚安嘛 [语音]")
        self.assertEqual(self.calls[0][0], "text")

    def test_daily_cap_blocks_second_voice(self):
        delivery.settings.voice_max = 1
        target = {"type": "private", "user_id": 1}
        delivery.send_qq(target, "qq:private:1", "第一句 [语音]")
        self.calls.clear()
        delivery.send_qq(target, "qq:private:1", "第二句 [语音]", user_text="发个语音")
        self.assertEqual([c[0] for c in self.calls], ["text"])


if __name__ == "__main__":
    unittest.main()
