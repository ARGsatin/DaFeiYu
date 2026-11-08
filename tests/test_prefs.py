"""设置面板的读写：只改该改的那几行，别把注释和顺序弄乱。

真正的 .env 不碰：把 prefs.ENV_FILE 指到临时目录再跑。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app import prefs
from app.config import settings


class PrefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"
        self.env.write_text(
            "# 注释要留着\n"
            "FISH_PROVIDER=api\n"
            "API_KEY=sk-old\n"
            "# FISH_LAT=31.23\n",
            encoding="utf-8",
        )
        self._old_env = prefs.ENV_FILE
        self._old_data = prefs.DATA_DIR
        prefs.ENV_FILE = self.env
        prefs.DATA_DIR = self.root / "data"
        self._old_settings = {
            "group_max_hour": settings.group_max_hour,
            "sky_lat": settings.sky_lat,
        }

    def tearDown(self):
        prefs.ENV_FILE = self._old_env
        prefs.DATA_DIR = self._old_data
        for attr, value in self._old_settings.items():
            setattr(settings, attr, value)
        os.environ["FISH_GROUP_MAX_HOUR"] = str(self._old_settings["group_max_hour"])
        self.tmp.cleanup()

    def test_write_keeps_comments_and_appends_missing(self):
        prefs.write_env({"FISH_PROVIDER": "ollama", "QQ_TOKEN": "abc"})
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("# 注释要留着", text)
        self.assertIn("FISH_PROVIDER=ollama", text)
        self.assertIn("QQ_TOKEN=abc", text)
        self.assertIn(prefs.BLOB_BEGIN, text)
        # 第二行还是第二行：顺序没乱
        self.assertEqual(text.splitlines()[1], "FISH_PROVIDER=ollama")

        # 再写一次不能又补一份
        prefs.write_env({"QQ_TOKEN": "xyz"})
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("QQ_TOKEN="), 1)
        self.assertIn("QQ_TOKEN=xyz", text)

    def test_saving_keeps_keys_added_by_earlier_save(self):
        # 尾巴区块里的键，不能因为这次保存没提到它就消失
        # （妹妹 QQ 号、主人 QQ 号就是这样被抹掉过好几回）
        prefs.write_env({"FISH_PROVIDER": "ollama", "QQ_TOKEN": "abc"})
        prefs.write_env({"FISH_GROUP_MAX_HOUR": 7})
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("QQ_TOKEN=abc", text)
        self.assertIn("FISH_GROUP_MAX_HOUR=7", text)
        # 而且只保留一份
        self.assertEqual(text.count("QQ_TOKEN="), 1)
        self.assertEqual(text.count(prefs.BLOB_BEGIN), 1)

    def test_snapshot_reads_file_not_runtime(self):
        # 「她的窗」会把城市解析成经纬度写回运行时；面板不能把坐标当成用户填的
        settings.sky_lat = 30.29
        snap = prefs.snapshot()
        self.assertEqual(snap["FISH_PROVIDER"], "api")
        self.assertEqual(snap["FISH_LAT"], 0.0)
        self.assertEqual(snap["API_KEY"], "sk-old")

    def test_apply_writes_only_changed(self):
        before = self.env.read_text(encoding="utf-8")
        result = prefs.apply({"FISH_PROVIDER": "api", "API_KEY": "sk-old"})
        self.assertEqual(result["changed"], [])
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)  # 没动就不写

        result = prefs.apply({"FISH_GROUP_MAX_HOUR": 9})
        self.assertEqual(result["changed"], ["FISH_GROUP_MAX_HOUR"])
        self.assertEqual(settings.group_max_hour, 9)
        self.assertIn("FISH_GROUP_MAX_HOUR=9",
                      self.env.read_text(encoding="utf-8"))

    def test_apply_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            prefs.apply({"FISH_GROUP_MAX_HOUR": "abc"})
        with self.assertRaises(ValueError):
            prefs.apply({"FISH_TTS": "nope"})
        with self.assertRaises(ValueError):
            prefs.apply({"FISH_NOT_A_KEY": 1})

    def test_apply_clamps_numbers(self):
        result = prefs.apply({"FISH_TEMPERATURE": 99})
        self.assertIn("FISH_TEMPERATURE", result["changed"])
        self.assertEqual(settings.temperature, 1.5)
        settings.temperature = 0.95
        os.environ["FISH_TEMPERATURE"] = "0.95"


if __name__ == "__main__":
    unittest.main()
