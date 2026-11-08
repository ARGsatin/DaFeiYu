"""QQ 收藏表情的同步：下载、标签、离线保留旧索引。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import qq_faces
from app.config import settings


class FakeResponse:
    def __init__(self, data=None, content: bytes = b"", status: int = 200):
        self._data = data
        self.content = content
        self.status_code = status
        self.headers = {"content-type": "image/gif"}
        self.text = ""

    def json(self):
        return self._data or {}


class QQFaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_dir, self.old_index = qq_faces.DIR, qq_faces.INDEX
        self.old_sync, self.old_post, self.old_get = (
            qq_faces._last_sync, qq_faces.requests.post, qq_faces.requests.get)
        qq_faces.DIR = Path(self.tmp.name) / "qq-faces"
        qq_faces.INDEX = qq_faces.DIR / "index.json"
        qq_faces._last_sync = 0.0
        self.item = {
            "md5": "A" * 32,
            "url": "https://example.invalid/face",
            "desc": "吃瓜 开心",
        }

    def tearDown(self):
        qq_faces.DIR, qq_faces.INDEX = self.old_dir, self.old_index
        qq_faces._last_sync = self.old_sync
        qq_faces.requests.post = self.old_post
        qq_faces.requests.get = self.old_get

    def _fake(self, items=None):
        items = [self.item] if items is None else items
        qq_faces.requests.post = lambda *a, **k: FakeResponse(
            {"status": "ok", "retcode": 0, "data": items})
        qq_faces.requests.get = lambda *a, **k: FakeResponse(
            content=b"GIF89a" + b"x" * 1000)

    def test_sync_downloads_and_tags_from_desc(self):
        self._fake()
        self.assertTrue(qq_faces.sync(force=True))
        files = list(qq_faces.DIR.glob("*.gif"))
        self.assertEqual(len(files), 1)
        self.assertEqual(qq_faces.count(), 1)
        self.assertEqual(set(qq_faces.tags()), {"吃瓜", "开心"})
        self.assertEqual(qq_faces.images("吃瓜"), files)
        self.assertEqual(qq_faces.images("QQ收藏"), files)

    def test_manual_tags_survive_next_sync(self):
        self._fake()
        qq_faces.sync(force=True)
        data = json.loads(qq_faces.INDEX.read_text(encoding="utf-8"))
        data["items"]["A" * 32]["tags"] = ["无语"]
        qq_faces.INDEX.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        qq_faces.sync(force=True)
        self.assertEqual(qq_faces.tags(), ["无语"])

    def test_removed_favorite_is_dropped_from_index(self):
        self._fake()
        qq_faces.sync(force=True)
        self._fake(items=[])
        qq_faces.sync(force=True)
        self.assertEqual(qq_faces.count(), 0)
        self.assertEqual(qq_faces.tags(), [])

    def test_offline_keeps_old_index(self):
        self._fake()
        qq_faces.sync(force=True)

        def down(*a, **k):
            raise RuntimeError("down")
        qq_faces.requests.post = down
        self.assertFalse(qq_faces.sync(force=True))
        self.assertEqual(qq_faces.count(), 1)


if __name__ == "__main__":
    unittest.main()
