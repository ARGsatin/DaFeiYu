"""NapCat 一键启动：命令拼得对、目录找得到、找不到时别炸。"""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from app import napcat
from app.config import settings


def _webui(dirpath: Path, port: int) -> None:
    cfg = dirpath / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "webui.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": port, "token": "t"}), encoding="utf-8"
    )


class NapcatTest(unittest.TestCase):
    def setUp(self):
        self._old_dir = settings.napcat_dir

    def tearDown(self):
        settings.napcat_dir = self._old_dir

    def test_launch_command_quotes_the_script(self):
        cmd = napcat.launch_command(Path("D:/NapCat"))
        self.assertEqual(cmd[0], "powershell.exe")
        inner = cmd[-1]
        # 提权跑、脚本路径带引号（目录里有空格也不会被拆开）
        self.assertIn("-Verb RunAs", inner)
        self.assertIn('"D:\\NapCat\\startNapCat.ps1"', inner)
        self.assertIn("-WorkingDirectory 'D:\\NapCat'", inner)

    def test_finds_configured_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "startNapCat.ps1").write_text("", encoding="utf-8")
            settings.napcat_dir = str(p)
            self.assertEqual(napcat.napcat_dir(), p)

    def test_bogus_dir_is_not_a_napcat(self):
        settings.napcat_dir = "C:/definitely-not-napcat"
        self.assertIsNone(napcat.napcat_dir())
        result = napcat.start()          # 找不到目录时只回一句话，不去拉进程
        self.assertFalse(result["ok"])
        self.assertIn("NapCat 目录", result["detail"])

    def test_qrcode_none_when_missing(self):
        settings.napcat_dir = "C:/definitely-not-napcat"
        self.assertIsNone(napcat.qrcode())

    def test_webui_up_false_when_port_closed(self):
        """NapCat 内核没起来（还停在登录界面）时，别把面板送到打不开的页面。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            _webui(d, port)          # 这个端口现在没人听
            self.assertFalse(napcat._webui_alive(d))

    def test_webui_up_true_when_someone_listens(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with socket.socket() as server:
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                _webui(d, server.getsockname()[1])
                self.assertTrue(napcat._webui_alive(d))

    def test_webui_up_false_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(napcat._webui_alive(Path(tmp)))


class SisterNapcatTest(unittest.TestCase):
    """面板里也要能起「鱼丸那份」NapCat：目录、端口都得指向第二份，不能碰鲸鲸的。"""

    def setUp(self):
        self._saved = {
            "sister_napcat": settings.sister_napcat,
            "napcat_dir": settings.napcat_dir,
            "sister_api": settings.sister_api,
            "sister_token": settings.sister_token,
        }
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.main = root / "NapCat"
        self.second = root / "NapCat2"
        for d in (self.main, self.second):
            d.mkdir()
            (d / "startNapCat.ps1").write_text("", encoding="utf-8")
        settings.napcat_dir = str(self.main)
        settings.sister_napcat = str(self.second)
        settings.sister_api = "http://127.0.0.1:59999"

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(settings, k, v)
        self._tmp.cleanup()

    def test_target_uses_second_dir(self):
        d, api, _tok = napcat._target(sister=True)
        self.assertEqual(d, self.second)
        self.assertEqual(api, "http://127.0.0.1:59999")

    def test_main_target_untouched(self):
        d, _api, _tok = napcat._target()
        self.assertEqual(d, self.main)

    def test_status_marks_the_second_one(self):
        st = napcat.status(timeout=0.2, sister=True)
        self.assertEqual(st["target"], "sister")
        self.assertTrue(st["found"])
        self.assertEqual(st["dir"], str(self.second))
        self.assertFalse(st["online"])      # 59999 上没人
        self.assertEqual(st["api"], "http://127.0.0.1:59999")

    def test_missing_second_dir_says_where_to_fill(self):
        settings.sister_napcat = "C:/definitely-not-napcat2"
        st = napcat.status(timeout=0.2, sister=True)
        self.assertFalse(st["found"])
        result = napcat.start(sister=True)
        self.assertFalse(result["ok"])
        self.assertIn("妹妹的 NapCat 目录", result["detail"])

    def test_qrcode_reads_second_cache(self):
        cache = self.second / "cache"
        cache.mkdir()
        (cache / "qrcode.png").write_bytes(b"x" * 200)
        self.assertEqual(napcat.qrcode(sister=True)[0], b"x" * 200)


if __name__ == "__main__":
    unittest.main()
