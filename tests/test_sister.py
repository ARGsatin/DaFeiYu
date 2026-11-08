"""妹妹那份数据：配置怎么推导、目录怎么建、怎么拉起来。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import sister
from app.config import settings


class EnvTest(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "sister_port": settings.sister_port, "sister_api": settings.sister_api,
            "sister_token": settings.sister_token, "sister_uin": settings.sister_uin,
            "self_uin": settings.self_uin,
        }
        settings.sister_port = 8766
        settings.sister_api = "http://127.0.0.1:3001"
        settings.sister_token = "tok"

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(settings, k, v)

    def test_overrides(self):
        values = sister.env_values(main_uin="10001")
        self.assertEqual(values["FISH_PORT"], "8766")
        self.assertEqual(values["QQ_API"], "http://127.0.0.1:3001")
        self.assertEqual(values["QQ_TOKEN"], "tok")
        self.assertEqual(values["FISH_PEER_UIN"], "10001")
        self.assertEqual(values["FISH_SESSION"], "sister")
        # 妹妹不能再养一个妹妹，也不能抢主号
        self.assertEqual(values["FISH_SISTER"], "0")
        self.assertEqual(values["FISH_PEER_OPEN"], "0")
        self.assertEqual(values["FISH_PEER_ONLY"], "1")
        self.assertEqual(values["FISH_PROACTIVE"], "0")
        self.assertEqual(values["FISH_NO_BROWSER"], "1")

    def test_env_text_has_header(self):
        text = sister._env_text({"A": "1"})
        self.assertIn("自动生成", text)
        self.assertIn("A=1", text)


class NapcatDirTest(unittest.TestCase):
    """妹妹那份 NapCat 必须指向另一个目录：指到鲸鲸那份会去跑会杀 QQ 的脚本。"""

    def setUp(self):
        self._saved = (settings.sister_napcat, settings.napcat_dir, settings.sister_port,
                       settings.sister_api, settings.sister_token, settings.sister_uin,
                       settings.self_uin)

    def tearDown(self):
        (settings.sister_napcat, settings.napcat_dir, settings.sister_port,
         settings.sister_api, settings.sister_token, settings.sister_uin,
         settings.self_uin) = self._saved

    def test_from_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "NapCat2"
            p.mkdir()
            (p / "NapCatWinBootMain.exe").write_bytes(b"x")
            settings.sister_napcat = str(p)
            self.assertEqual(sister.napcat_dir(), str(p))
            # 写进妹妹那份 .env，面板上的「启动 NapCat」才会去对地方
            values = sister.env_values()
            self.assertEqual(values["NAPCAT_DIR"], str(p))

    def test_auto_finds_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "NapCat"
            second = Path(tmp) / "NapCat2"
            main.mkdir()
            second.mkdir()
            (second / "startNapCat.ps1").write_text("# x", encoding="utf-8")
            settings.sister_napcat = ""
            settings.napcat_dir = str(main)
            self.assertEqual(sister.napcat_dir(), str(second))

    def test_not_confused_with_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "NapCat"
            main.mkdir()
            (main / "startNapCat.ps1").write_text("# x", encoding="utf-8")
            settings.sister_napcat = ""
            settings.napcat_dir = str(main)
            # 旁边没有第二份：宁可空着，也不能回落到鲸鲸那份
            self.assertEqual(sister.napcat_dir(), "")
            self.assertNotIn("NAPCAT_DIR", sister.env_values())


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = sister.PROFILE
        self._saved_pid = sister.PID_FILE
        sister.PROFILE = Path(self._tmp.name) / "sister"
        sister.PID_FILE = Path(self._tmp.name) / "sister.pid"

    def tearDown(self):
        sister.PROFILE = self._saved
        sister.PID_FILE = self._saved_pid
        self._tmp.cleanup()

    def test_ensure_profile(self):
        result = sister.ensure_profile(main_uin="10001")
        self.assertTrue((sister.PROFILE / "persona.md").is_file())
        env = (sister.PROFILE / ".env").read_text(encoding="utf-8")
        self.assertIn("FISH_PEER_UIN=10001", env)
        self.assertIn("FISH_SISTER=0", env)
        self.assertIn("persona.md", result["made"])

    def test_ensure_profile_keeps_persona(self):
        sister.PROFILE.mkdir(parents=True)
        (sister.PROFILE / "persona.md").write_text("我自己写的", encoding="utf-8")
        sister.ensure_profile(main_uin="10001")
        self.assertEqual((sister.PROFILE / "persona.md").read_text(encoding="utf-8"),
                         "我自己写的")

    def test_env_rewritten_on_port_change(self):
        sister.ensure_profile(main_uin="10001")
        settings.sister_port = 9999
        try:
            sister.ensure_profile(main_uin="10001")
            env = (sister.PROFILE / ".env").read_text(encoding="utf-8")
            self.assertIn("FISH_PORT=9999", env)
        finally:
            settings.sister_port = 8766

    def test_status_not_running(self):
        self._saved_health = sister.healthy
        self._saved_login = sister.login_info
        self._saved_api = settings.sister_api
        sister.healthy = lambda *a, **k: None
        sister.login_info = lambda *a, **k: {}
        settings.sister_api = ""      # 别真的去连 3001
        try:
            st = sister.status()
            self.assertFalse(st["running"])
            self.assertEqual(st["pid"], 0)
            self.assertTrue(st["url"].startswith("http://"))
        finally:
            sister.healthy = self._saved_health
            sister.login_info = self._saved_login
            settings.sister_api = self._saved_api


class LaunchTest(unittest.TestCase):
    def test_command(self):
        cmd = sister.launch_command()
        self.assertEqual(cmd[-2:], ["-m", "app"])

    def test_env_points_home(self):
        env = sister.launch_env()
        self.assertEqual(Path(env["FISH_HOME"]), sister.PROFILE)
        self.assertEqual(env["FISH_NO_BROWSER"], "1")


if __name__ == "__main__":
    unittest.main()
