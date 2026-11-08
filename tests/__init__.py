"""大肥鱼的测试：不联网、不碰真数据。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app import pause

# 用户面板里可能正开着「暂停」；测试一律从没暂停开始，也别动真实的 data/pause.json
_PAUSE_TMP = tempfile.TemporaryDirectory()
pause.STATE_FILE = Path(_PAUSE_TMP.name) / "pause.json"
pause._state = {"paused": False, "since": 0.0}
