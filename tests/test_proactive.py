"""她主动找你：什么时候该说、什么时候该闭嘴、说了什么。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from app import proactive
from app.config import settings


def base_data(**kw) -> dict:
    now = time.time()
    data = {
        "last_user_id": "2953133421",
        "last_session": "qq:private:2953133421",
        "last_seen": now - 5 * 3600,  # 5 小时没说话
        "last_sent": now - 10 * 3600,  # 10 小时没主动过
        "sent_today": 0,
        "silent_streak": 0,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    data.update(kw)
    return data


class DecideTest(unittest.TestCase):
    def setUp(self):
        self._saved = (settings.proactive, settings.proactive_max, settings.proactive_gap,
                       settings.proactive_idle, settings.proactive_quiet)
        settings.proactive = True
        settings.proactive_max = 2
        settings.proactive_gap = 3
        settings.proactive_idle = 4
        settings.proactive_quiet = "23-8"

    def tearDown(self):
        (settings.proactive, settings.proactive_max, settings.proactive_gap,
         settings.proactive_idle, settings.proactive_quiet) = self._saved

    @staticmethod
    def at(hour: int = 15, minute: int = 0) -> datetime:
        """用的是「今天」，不然每天日期一变，日限额那几条就测不到。"""
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    def test_should_speak_after_long_silence(self):
        ok, why = proactive.decide(data=base_data(), dt=self.at())
        self.assertTrue(ok, why)

    def test_off_switch(self):
        settings.proactive = False
        ok, why = proactive.decide(data=base_data(), dt=self.at())
        self.assertFalse(ok)
        self.assertIn("关", why)

    def test_quiet_hours(self):
        for hour in (23, 0, 7):
            ok, why = proactive.decide(data=base_data(), dt=self.at(hour, 30))
            self.assertFalse(ok, hour)
            self.assertIn("免打扰", why)

    def test_daily_limit(self):
        data = base_data(sent_today=2)
        ok, why = proactive.decide(data=data, dt=self.at())
        self.assertFalse(ok)
        self.assertIn("说够", why)

    def test_daily_limit_resets_next_day(self):
        data = base_data(sent_today=2, date="2026-11-01")
        ok, _why = proactive.decide(data=data, dt=self.at())
        self.assertTrue(ok)

    def test_min_gap(self):
        data = base_data(last_sent=time.time() - 3600)
        ok, why = proactive.decide(data=data, dt=self.at())
        self.assertFalse(ok)
        self.assertIn("刚说", why)

    def test_still_chatting(self):
        data = base_data(last_seen=time.time() - 3600)
        ok, why = proactive.decide(data=data, dt=self.at())
        self.assertFalse(ok)
        self.assertIn("刚聊", why)

    def test_backoff_without_reply(self):
        data = base_data(silent_streak=3)
        ok, why = proactive.decide(data=data, dt=self.at())
        self.assertFalse(ok)
        self.assertIn("没回音", why)

    def test_no_target_yet(self):
        data = base_data(last_user_id=None)
        ok, why = proactive.decide(data=data, dt=self.at())
        self.assertFalse(ok)
        self.assertIn("发给谁", why)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = proactive.STATE_FILE
        proactive.STATE_FILE = Path(self.tmp.name) / "proactive.json"

    def tearDown(self):
        proactive.STATE_FILE = self._saved
        self.tmp.cleanup()

    def test_incoming_resets_streak(self):
        proactive.note_outgoing("在吗")
        proactive.note_outgoing("在吗")
        self.assertEqual(proactive.state()["silent_streak"], 2)
        proactive.note_incoming("123", "qq:private:123")
        st = proactive.state()
        self.assertEqual(st["silent_streak"], 0)
        self.assertEqual(st["last_user_id"], "123")

    def test_outgoing_counts_per_day(self):
        proactive.note_outgoing("早呀")
        st = proactive.state()
        self.assertEqual(st["sent_today"], 1)
        self.assertEqual(st["last_text"], "早呀")

    def test_state_file_is_json(self):
        proactive.note_incoming("1", "qq:private:1")
        raw = json.loads(proactive.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(raw["last_user_id"], "1")

    def test_follow_counts_separately(self):
        proactive.note_incoming("1", "qq:private:1")
        proactive.note_outgoing("人呢", follow=True)
        st = proactive.state()
        self.assertEqual(int(st["follow_today"]), 1)
        self.assertEqual(int(st["follow_streak"]), 1)
        self.assertEqual(int(st["sent_today"]), 1)
        self.assertIn("follow_sent_at", st)

    def test_incoming_clears_follow_plan(self):
        proactive.note_incoming("1", "qq:private:1")
        proactive.note_outgoing("人呢", follow=True)
        proactive.note_incoming("1", "qq:private:1")
        st = proactive.state()
        self.assertEqual(int(st["follow_streak"]), 0)
        self.assertNotIn("follow_at", st)
        self.assertNotIn("follow_sent_at", st)


class FollowTest(unittest.TestCase):
    """你没接话的时候，她会自己补一句——但不会追着说。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved_file = proactive.STATE_FILE
        proactive.STATE_FILE = Path(self.tmp.name) / "proactive.json"
        self._saved = (settings.proactive, settings.proactive_follow,
                       settings.proactive_follow_idle, settings.proactive_follow_window,
                       settings.proactive_follow_max, settings.proactive_quiet)
        settings.proactive = True
        settings.proactive_follow = True
        settings.proactive_follow_idle = 15
        settings.proactive_follow_window = 25
        settings.proactive_follow_max = 2
        settings.proactive_quiet = "23-8"

    def tearDown(self):
        proactive.STATE_FILE = self._saved_file
        (settings.proactive, settings.proactive_follow,
         settings.proactive_follow_idle, settings.proactive_follow_window,
         settings.proactive_follow_max, settings.proactive_quiet) = self._saved
        self.tmp.cleanup()

    @staticmethod
    def data(minutes: float = 20, **kw) -> dict:
        now = time.time()
        last_seen = now - minutes * 60
        d = {
            "last_session": "qq:private:1", "last_user_id": "1",
            "last_seen": last_seen, "last_reply_at": last_seen + 2,
            "last_sent": now - 5 * 3600, "sent_today": 0, "follow_today": 0,
            "silent_streak": 0, "follow_streak": 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
            # 这一轮沉默里，她打算在 16 分钟的时候补
            "follow_at": {"anchor": last_seen, "epoch": last_seen + 16 * 60},
        }
        d.update(kw)
        return d

    @staticmethod
    def noon() -> datetime:
        return datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    def test_follows_up_after_short_silence(self):
        ok, why = proactive.followup(data=self.data(20), dt=self.noon())
        self.assertTrue(ok, why)
        self.assertIn("没接话", why)

    def test_not_while_still_chatting(self):
        ok, why = proactive.followup(data=self.data(5), dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("热乎", why)

    def test_gives_up_after_a_few_hours(self):
        ok, why = proactive.followup(data=self.data(200), dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("过去了", why)

    def test_waits_for_his_reply(self):
        # 他刚说完、她还没回上：这时候补话就成自言自语了
        ok, why = proactive.followup(data=self.data(20, last_reply_at=0), dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("还没回上话", why)

    def test_only_once_per_silence(self):
        d = self.data(20)
        d["follow_sent_at"] = d["last_seen"] + 60
        ok, why = proactive.followup(data=d, dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("已经补过", why)

    def test_daily_cap(self):
        ok, why = proactive.followup(data=self.data(20, follow_today=2), dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("补够", why)

    def test_stops_after_two_ignored(self):
        ok, why = proactive.followup(data=self.data(20, follow_streak=2), dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("补了两次", why)

    def test_quiet_at_night(self):
        dt = self.noon().replace(hour=23, minute=30)
        ok, why = proactive.followup(data=self.data(20), dt=dt)
        self.assertFalse(ok)
        self.assertIn("免打扰", why)

    def test_web_session_needs_no_qq_id(self):
        ok, why = proactive.followup(
            data=self.data(20, last_session="main", last_user_id=None), dt=self.noon()
        )
        self.assertTrue(ok, why)

    def test_waits_for_the_planned_moment(self):
        d = self.data(20)
        d["follow_at"] = {"anchor": d["last_seen"], "epoch": time.time() + 600}
        ok, why = proactive.followup(data=d, dt=self.noon())
        self.assertFalse(ok)
        self.assertIn("再等等", why)


class ComposeTest(unittest.TestCase):
    class Fish:
        @staticmethod
        def system_prompt(session):
            return "你是大肥鱼。"

    class Mem:
        @staticmethod
        def recent(session, n=8):
            return [{"role": "user", "content": "晚安"}, {"role": "assistant", "content": "早点睡"}]

    class Brain:
        def __init__(self, out):
            self.out = out

        def complete(self, messages, temperature=0.5, max_tokens=200):
            self.seen = messages
            return self.out

    def test_skip_when_nothing_to_say(self):
        brain = self.Brain("（不说）")
        self.assertIsNone(
            proactive.compose(self.Fish(), self.Mem(), brain, "main", base_data())
        )

    def test_use_text_when_she_has_something(self):
        brain = self.Brain("「在干嘛呀，一天没动静了」")
        text = proactive.compose(self.Fish(), self.Mem(), brain, "main", base_data())
        self.assertEqual(text, "在干嘛呀，一天没动静了")
        self.assertIn("主人上次跟你说话", brain.seen[-1]["content"])

    def test_follow_prompt_says_it_is_a_follow_up(self):
        brain = self.Brain("人呢")
        proactive.compose(self.Fish(), self.Mem(), brain, "main", base_data(), kind="follow")
        prompt = brain.seen[-1]["content"]
        self.assertIn("没声了", prompt)
        self.assertIn("分钟没接话", prompt)


class SlotTest(unittest.TestCase):
    """早晚定时：一天一次，窗口内才发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved_file = proactive.STATE_FILE
        proactive.STATE_FILE = Path(self.tmp.name) / "proactive.json"
        self._saved = (settings.proactive, settings.proactive_slots, settings.proactive_quiet,
                       settings.proactive_morning, settings.proactive_night,
                       settings.proactive_slot_window)
        settings.proactive = True
        settings.proactive_slots = True
        settings.proactive_quiet = "23-8"
        settings.proactive_morning = "08:00"
        settings.proactive_night = "22:00"
        settings.proactive_slot_window = 60

    def tearDown(self):
        proactive.STATE_FILE = self._saved_file
        (settings.proactive, settings.proactive_slots, settings.proactive_quiet,
         settings.proactive_morning, settings.proactive_night,
         settings.proactive_slot_window) = self._saved
        self.tmp.cleanup()

    @staticmethod
    def data(slot_at: datetime | None = None, slot: str = "morning", **kw) -> dict:
        today = "2026-11-02"
        data = {"last_user_id": "123", "last_session": "qq:private:123",
                "last_seen": time.time() - 3600,
                "last_sent": time.time() - 5 * 3600, "sent_today": 0,
                "silent_streak": 0, "date": today}
        if slot_at is not None:
            data[f"{slot}_at"] = {"date": today, "epoch": slot_at.timestamp()}
        data.update(kw)
        return data

    def test_morning_due_inside_window(self):
        now = datetime(2026, 11, 2, 8, 30)
        data = self.data(datetime(2026, 11, 2, 8, 10))
        self.assertEqual(proactive.due_slot(now=now.timestamp(), data=data, dt=now), "morning")

    def test_night_due_inside_window(self):
        now = datetime(2026, 11, 2, 22, 20)
        data = self.data(datetime(2026, 11, 2, 22, 5), slot="night")
        self.assertEqual(proactive.due_slot(now=now.timestamp(), data=data, dt=now), "night")

    def test_not_due_before_target(self):
        now = datetime(2026, 11, 2, 8, 5)
        data = self.data(datetime(2026, 11, 2, 8, 40))
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data=data, dt=now))

    def test_too_late_does_not_backfill(self):
        # 11 点才开机，不该补一句「早安」
        now = datetime(2026, 11, 2, 11, 0)
        data = self.data(datetime(2026, 11, 2, 8, 10))
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data=data, dt=now))

    def test_only_once_a_day(self):
        now = datetime(2026, 11, 2, 8, 30)
        data = self.data(datetime(2026, 11, 2, 8, 10), morning_done="2026-11-02")
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data=data, dt=now))

    def test_off_switches(self):
        now = datetime(2026, 11, 2, 8, 30)
        data = self.data(datetime(2026, 11, 2, 8, 10))
        settings.proactive_slots = False
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data=data, dt=now))
        settings.proactive_slots = True
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data={**data, "enabled": False}, dt=now))
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data={**data, "last_user_id": None}, dt=now))

    def test_quiet_hours_block_night(self):
        now = datetime(2026, 11, 2, 23, 30)
        data = self.data(datetime(2026, 11, 2, 23, 10), slot="night")
        self.assertIsNone(proactive.due_slot(now=now.timestamp(), data=data, dt=now))

    def test_slot_target_is_stable_within_day(self):
        dt = datetime(2026, 11, 2, 7, 0)
        first = proactive.slot_plan("morning", dt, {})
        second = proactive.slot_plan("morning", dt, proactive.state())
        self.assertAlmostEqual(first, second, places=3)
        start = datetime(2026, 11, 2, 8, 0).timestamp()
        self.assertTrue(start <= first < start + 3600)

    def test_slot_does_not_eat_idle_budget(self):
        proactive.note_outgoing("早呀", slot="morning")
        st = proactive.state()
        self.assertEqual(st["morning_done"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(int(st.get("sent_today") or 0), 0)
        self.assertEqual(int(st.get("silent_streak") or 0), 0)

    def test_note_outgoing_without_slot_counts(self):
        proactive.note_outgoing("在吗")
        st = proactive.state()
        self.assertEqual(int(st["sent_today"]), 1)
        self.assertEqual(int(st["silent_streak"]), 1)


class SpeakTest(unittest.TestCase):
    """网页会话：她主动说的话写进库里，等页面轮询；不用 NapCat。"""

    class Fish:
        @staticmethod
        def system_prompt(session):
            return "你是大肥鱼。"

    class Mem:
        def __init__(self):
            self.added = []

        @staticmethod
        def recent(session, n=8):
            return []

        def add(self, session, role, content):
            self.added.append((session, role, content))
            return 99

    class Brain:
        @staticmethod
        def complete(messages, temperature=0.5, max_tokens=200):
            return "人呢，我把泡泡数到一百了"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = proactive.STATE_FILE
        proactive.STATE_FILE = Path(self.tmp.name) / "proactive.json"
        now = time.time()
        proactive._write({
            "last_session": "main", "last_seen": now - 600, "last_reply_at": now - 590,
            "date": datetime.now().strftime("%Y-%m-%d"),
        })

    def tearDown(self):
        proactive.STATE_FILE = self._saved
        self.tmp.cleanup()

    def test_web_message_is_stored_for_polling(self):
        mem = self.Mem()
        result = proactive.speak_once(self.Fish(), mem, self.Brain(), force=True)
        self.assertTrue(result["sent"], result)
        self.assertEqual(result["id"], 99)
        self.assertEqual(mem.added, [("main", "assistant", "人呢，我把泡泡数到一百了")])
        self.assertEqual(proactive.state()["last_text"], "人呢，我把泡泡数到一百了")


if __name__ == "__main__":
    unittest.main()
