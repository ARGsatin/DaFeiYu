"""跟妹妹聊天的那套刹车：什么时候能回、什么时候该装没看见、什么时候主动找她。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from app import peer
from app.config import settings


class PeerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_file = peer.STATE_FILE
        peer.STATE_FILE = Path(self._tmp.name) / "peer.json"
        self._saved = {
            "peer_uin": settings.peer_uin, "sister": settings.sister,
            "sister_uin": settings.sister_uin, "peer_open": settings.peer_open,
            "peer_max": settings.peer_max, "peer_cooldown": settings.peer_cooldown,
            "peer_day": settings.peer_day, "peer_gap": settings.peer_gap,
            "peer_quiet": settings.peer_quiet, "peer_open_gap": settings.peer_open_gap,
            "peer_open_day": settings.peer_open_day,
        }
        settings.peer_uin = "10001"
        settings.sister = False
        settings.sister_uin = ""
        settings.peer_open = True
        settings.peer_max = 3
        settings.peer_cooldown = 90
        settings.peer_day = 20
        settings.peer_gap = 4
        settings.peer_quiet = "23-9"
        settings.peer_open_gap = 3
        settings.peer_open_day = 2

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(settings, k, v)
        peer.STATE_FILE = self._saved_file
        self._tmp.cleanup()

    def at(self, hour: int = 15, minute: int = 0) -> datetime:
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


class ReplyBudgetTest(PeerTestBase):
    def test_fresh_can_reply(self):
        ok, why = peer.may_reply()
        self.assertTrue(ok, why)
    def test_stops_after_max_turns(self):
        for _ in range(3):
            peer.note_incoming()
            ok, _ = peer.may_reply()
            self.assertTrue(ok)
            peer.note_reply()
        peer.note_incoming()
        ok, why = peer.may_reply()
        self.assertFalse(ok)
        self.assertIn("聊够", why)

    def test_cooldown_expires(self):
        now = time.time()
        for _ in range(3):
            peer.note_incoming(now)
            peer.note_reply(now)
        peer.note_incoming(now)
        self.assertFalse(peer.may_reply(now)[0])
        # 冷却还没过
        self.assertFalse(peer.may_reply(now + 60)[0])
        # 过了冷却，新的一轮
        later = now + 91 * 60
        peer.note_incoming(later)
        self.assertTrue(peer.may_reply(later)[0])

    def test_new_burst_after_quiet_gap(self):
        now = time.time()
        for _ in range(3):
            peer.note_incoming(now)
            peer.note_reply(now)
        # 隔了一个多小时再说话：算新的一轮，额度重新给
        later = now + (peer.BURST_WINDOW_MIN + 5) * 60
        peer.note_incoming(later)
        self.assertTrue(peer.may_reply(later)[0])

    def test_day_cap(self):
        now = time.time()
        st = {"day": datetime.now().strftime("%Y-%m-%d"), "day_count": 20}
        peer._write({"10001": st})
        ok, why = peer.may_reply(now)
        self.assertFalse(ok)
        self.assertIn("今天", why)

    def test_day_rolls_over(self):
        peer._write({"10001": {"day": "2000-01-01", "day_count": 999}})
        self.assertTrue(peer.may_reply()[0])

    def test_paused_blocks(self):
        peer.pause.set_paused(True)
        try:
            ok, why = peer.may_reply()
            self.assertFalse(ok)
        finally:
            peer.pause.set_paused(False)

    def test_no_peer_uin(self):
        settings.peer_uin = ""
        settings.sister = False
        ok, why = peer.may_reply()
        self.assertFalse(ok)
        self.assertIn("QQ", why)


class NamedTest(PeerTestBase):
    def test_peer_uin_from_sister(self):
        settings.peer_uin = ""
        settings.sister = True
        settings.sister_uin = "20002"
        self.assertEqual(peer.peer_uin(), "20002")
        self.assertTrue(peer.is_peer(20002))
        self.assertTrue(peer.is_peer("20002"))
        self.assertFalse(peer.is_peer("30003"))
        self.assertEqual(peer.session_for(), "qq:private:20002")
        self.assertTrue(peer.is_peer_session("qq:private:20002"))
        self.assertFalse(peer.is_peer_session("qq:group:20002"))


class OpenTest(PeerTestBase):
    def test_open_allowed_when_never_opened(self):
        ok, why = peer.open_due(dt=self.at())
        self.assertTrue(ok, why)

    def test_open_quiet_hours(self):
        for hour in (23, 0, 7, 8):
            ok, why = peer.open_due(dt=self.at(hour))
            self.assertFalse(ok, why)
            self.assertIn("免打扰", why)

    def test_open_gap(self):
        peer.note_open()
        ok, why = peer.open_due(dt=self.at())
        self.assertFalse(ok)
        self.assertIn("等", why)

    def test_open_day_cap(self):
        peer.note_open()
        peer.note_open()
        # 把「刚才聊过」那条挪开，单独看每天次数
        data = peer._read()
        data["10001"].pop("last_in", None)
        data["10001"]["last_open"] = 0
        peer._write(data)
        ok, why = peer.open_due(dt=self.at())
        self.assertFalse(ok)
        self.assertIn("够多", why)

    def test_open_after_recent_chat(self):
        peer.note_incoming()
        ok, why = peer.open_due(dt=self.at())
        self.assertFalse(ok)
        self.assertIn("刚聊过", why)

    def test_open_switch_off(self):
        settings.peer_open = False
        ok, why = peer.open_due(dt=self.at())
        self.assertFalse(ok)


class StatsTest(PeerTestBase):
    def test_stats_counters(self):
        peer.note_incoming()
        peer.note_reply()
        st = peer.stats()
        self.assertEqual(st["uin"], "10001")
        self.assertEqual(st["burst_used"], 1)
        self.assertEqual(st["day_used"], 1)
        self.assertEqual(st["max"], 3)

    def test_reset(self):
        peer.note_incoming()
        peer.note_reply()
        peer.reset()
        self.assertEqual(peer.stats()["burst_used"], 0)


class WhoMayAnswerTest(PeerTestBase):
    """妹妹那份数据只认姐姐和主人；陌生人一律不理。"""

    OWNER = "2953133421"

    def setUp(self):
        super().setUp()
        self._extra = (settings.peer_only, settings.owner_uin)
        settings.peer_uin = "3877365065"     # 姐姐
        settings.peer_only = True
        settings.owner_uin = self.OWNER

    def tearDown(self):
        settings.peer_only, settings.owner_uin = self._extra
        super().tearDown()

    def test_peer_bot_always_answered(self):
        self.assertTrue(peer.may_answer("3877365065"))

    def test_owner_answered(self):
        # 主人自己发她也回——不然她永远不认识养她的人
        self.assertTrue(peer.may_answer(self.OWNER))

    def test_stranger_ignored(self):
        self.assertFalse(peer.may_answer("2229518088"))

    def test_group_still_answered(self):
        # 群里不按这个规则，仍走群聊那套
        self.assertTrue(peer.may_answer("2229518088", is_group=True))

    def test_no_owner_configured_means_only_peer(self):
        settings.owner_uin = ""
        self.assertFalse(peer.may_answer(self.OWNER))

    def test_peer_only_off_answers_everyone(self):
        settings.peer_only = False
        self.assertTrue(peer.may_answer("2229518088"))


class RelationshipTest(PeerTestBase):
    """她是谁的妹妹/姐姐，得写进提示词；记忆按会话分区，光靠回想会答「不知道」。"""

    def test_whale_knows_her_sister(self):
        settings.sister = True
        settings.peer_uin = ""            # 鲸鲸这边从「妹妹的 QQ」推
        settings.sister_uin = "3318896881"
        hint = peer.relationship_hint()
        self.assertIn("鱼丸", hint)
        self.assertIn("3318896881", hint)
        self.assertIn("妹妹", hint)

    def test_sister_knows_her_whale(self):
        settings.sister = False
        settings.peer_uin = "3877365065"
        hint = peer.relationship_hint()
        self.assertIn("鲸鲸", hint)
        self.assertIn("姐姐", hint)

    def test_no_peer_no_hint(self):
        settings.sister = False
        settings.peer_uin = ""
        self.assertEqual(peer.relationship_hint(), "")

    def test_owner_chat_also_recalls_peer_session(self):
        settings.sister = True
        settings.peer_uin = ""
        settings.sister_uin = "3318896881"
        scopes = peer.recall_scopes("qq:private:2953133421")
        self.assertEqual(scopes, ["qq:private:2953133421", "qq:private:3318896881"])

    def test_peer_chat_does_not_recall_owner(self):
        settings.sister = True
        settings.peer_uin = ""
        settings.sister_uin = "3318896881"
        scopes = peer.recall_scopes("qq:private:3318896881")
        self.assertEqual(scopes, ["qq:private:3318896881"])

    def test_group_does_not_pull_private_memories(self):
        settings.sister = True
        settings.peer_uin = ""
        settings.sister_uin = "3318896881"
        scopes = peer.recall_scopes("qq:group:235069995")
        self.assertEqual(scopes, ["qq:group:235069995"])

    def test_empty_session(self):
        self.assertEqual(peer.recall_scopes(""), [])


if __name__ == "__main__":
    unittest.main()
