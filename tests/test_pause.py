"""手动暂停：状态能存住，暂停时主动 / 群聊 / 发送都闭嘴。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import delivery, groupchat, pause, proactive


class PauseTest(unittest.TestCase):
    def setUp(self):
        self._old_file = pause.STATE_FILE
        self._old_state = dict(pause._state)
        self._tmp = tempfile.TemporaryDirectory()
        pause.STATE_FILE = Path(self._tmp.name) / "pause.json"
        pause._state = {"paused": False, "since": 0.0}

    def tearDown(self):
        pause.STATE_FILE = self._old_file
        pause._state = self._old_state
        self._tmp.cleanup()

    def test_toggle_and_persist(self):
        state = pause.set_paused(True)
        self.assertTrue(state["paused"])
        self.assertGreater(state["since"], 0)
        self.assertTrue(pause.is_paused())

        saved = json.loads(pause.STATE_FILE.read_text(encoding="utf-8"))
        self.assertTrue(saved["paused"])
        self.assertGreater(saved["since"], 0)

        state = pause.set_paused(False)
        self.assertFalse(state["paused"])
        self.assertEqual(state["since"], 0)
        self.assertFalse(pause.is_paused())

    def test_reload_keeps_paused(self):
        pause.set_paused(True)
        pause._state = {"paused": False, "since": 0.0}
        self.assertTrue(pause.reload()["paused"])
        self.assertTrue(pause.is_paused())

    def test_bad_since_still_loads(self):
        pause.STATE_FILE.write_text('{"paused": true, "since": "坏了"}', encoding="utf-8")
        state = pause.reload()
        self.assertTrue(state["paused"])
        self.assertGreater(state["since"], 0)

    def test_speakers_stay_silent(self):
        pause.set_paused(True)
        self.assertIn("暂停", proactive.speak_once(None, None, None)["reason"])
        self.assertIn("暂停", groupchat.speak_once(None, None, 1)["reason"])
        self.assertEqual(
            delivery.send_qq({"type": "private", "user_id": "1"}, "qq:private:1", "在吗")["sent"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
