"""桌面启动器的小测：自启命令拼得对、探活不会误报。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import desktop


class DesktopTest(unittest.TestCase):
    def test_autostart_target_points_at_desktop(self):
        if getattr(sys, "frozen", False):
            self.skipTest("打包成 exe 之后命令是 exe 自己")
        target = desktop.autostart_target()
        self.assertIn("desktop.py", target)
        self.assertIn("--quiet", target)
        self.assertTrue(target.startswith('"'))     # 路径带空格也不会被拆开

    def test_service_up_false_on_dead_port(self):
        old = desktop.URL
        desktop.URL = "http://127.0.0.1:9"          # discard 口，正常不会有人监听
        try:
            self.assertFalse(desktop.service_up(timeout=0.3))
        finally:
            desktop.URL = old

    def test_edge_path_is_absolute_if_found(self):
        edge = desktop.find_edge()
        if edge is None:
            self.skipTest("这台机器没装 Edge")
        self.assertTrue(Path(edge).is_absolute())


if __name__ == "__main__":
    unittest.main()
