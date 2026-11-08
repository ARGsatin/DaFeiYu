"""群聊自由发言：什么时候能说、说几句、说不说，以及别被自己话套进去。"""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from app import groupchat
from app.adapters import qq
from app.config import settings


class GroupChatBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved_file = groupchat.STATE_FILE
        groupchat.STATE_FILE = Path(self.tmp.name) / "groupchat.json"
        self._saved = (settings.group_chat, settings.group_allow,
                       settings.group_max_hour, settings.group_gap,
                       settings.group_max_day, settings.group_rate,
                       settings.group_chime)
        settings.group_chat = True
        settings.group_allow = ""
        settings.group_max_hour = 6
        settings.group_gap = 3
        settings.group_max_day = 15
        settings.group_rate = 6
        settings.group_chime = True

    def tearDown(self):
        groupchat.STATE_FILE = self._saved_file
        (settings.group_chat, settings.group_allow, settings.group_max_hour,
         settings.group_gap, settings.group_max_day,
         settings.group_rate, settings.group_chime) = self._saved
        self.tmp.cleanup()

    @staticmethod
    def now(hour: int = 15, minute: int = 0) -> datetime:
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def real_now() -> datetime:
        """跟 time.time() 对齐的当前时刻：限流用的是真实时钟，别拿一个过去的时间去比。"""
        return datetime.now().replace(second=0, microsecond=0)

    def later(self, minutes: float, dt: datetime) -> float:
        """从 dt 起过 minutes 分钟，换算成 epoch。"""
        return dt.timestamp() + minutes * 60


class WhitelistTest(GroupChatBase):
    def test_all_groups_by_default(self):
        ok, _ = groupchat.allowed(235069995)
        self.assertTrue(ok)

    def test_star_means_all(self):
        settings.group_allow = "*"
        self.assertTrue(groupchat.allowed(1)[0])

    def test_only_listed_groups(self):
        settings.group_allow = "235069995, 123456"
        self.assertTrue(groupchat.allowed(235069995)[0])
        self.assertTrue(groupchat.allowed("123456")[0])
        self.assertFalse(groupchat.allowed(999)[0])

    def test_off_switch(self):
        settings.group_chat = False
        ok, why = groupchat.allowed(1)
        self.assertFalse(ok)
        self.assertIn("关着", why)
        ok, why = groupchat.should_speak(1, dt=self.now())
        self.assertFalse(ok)


class RateLimitTest(GroupChatBase):
    GID = 235069995

    def test_quiet_when_never_spoken(self):
        ok, _ = groupchat.should_speak(self.GID, dt=self.real_now())
        self.assertTrue(ok)

    def test_min_gap_between_messages(self):
        now = self.real_now()
        groupchat.note_spoke(self.GID, "接一句", slot=now)
        ok, why = groupchat.should_speak(self.GID, now=self.later(1, now), dt=now)
        self.assertFalse(ok)
        self.assertIn("刚说", why)

    def test_gap_expires(self):
        now = self.real_now()
        groupchat.note_spoke(self.GID, "接一句", slot=now)
        ok, _ = groupchat.should_speak(self.GID, now=self.later(settings.group_gap + 1, now), dt=now)
        self.assertTrue(ok)

    def test_hourly_cap(self):
        now = self.real_now()
        st = {"groups": {str(self.GID): {
            "day": now.strftime("%Y-%m-%d"), "today": 2,
            "hour_count": settings.group_max_hour, "hour_stamp": now.hour,
            "last_spoke": 0, "ignored": 0,
        }}}
        groupchat._write(st)
        ok, why = groupchat.should_speak(self.GID, now=now.timestamp(), dt=now, data=st)
        self.assertFalse(ok)
        self.assertIn("小时", why)

    def test_daily_cap(self):
        now = self.real_now()
        st = {"groups": {str(self.GID): {
            "day": now.strftime("%Y-%m-%d"), "today": settings.group_max_day,
            "last_spoke": 0, "ignored": 0,
        }}}
        groupchat._write(st)
        ok, why = groupchat.should_speak(self.GID, now=now.timestamp(), dt=now, data=st)
        self.assertFalse(ok)
        self.assertIn("今天", why)

    def test_day_resets_counts(self):
        now = self.real_now()
        # 昨天的计数不该压到今天头上
        st = {"groups": {str(self.GID): {
            "day": "2000-01-01", "today": 99, "hour_count": 99,
            "hour_stamp": now.hour, "last_spoke": 0, "ignored": 0,
        }}}
        groupchat._write(st)
        ok, why = groupchat.should_speak(self.GID, now=now.timestamp(), dt=now)
        self.assertTrue(ok, why)
        # 今天第一次开口，从新的一天重新计数
        groupchat.note_spoke(self.GID, "今天第一句", slot=now)
        self.assertEqual(groupchat.state(self.GID)["today"], 1)

    def test_shuts_up_when_ignored(self):
        now = self.real_now()
        # 只测「没人理」这条：把小时/每日额度放宽，别让别的规则先说够
        settings.group_max_hour = 99
        settings.group_max_day = 99
        for i, t in enumerate(["一", "二", "三"]):
            groupchat.note_spoke(self.GID, t, slot=now)
        # 连着几句没人接：先闭嘴
        ok, why = groupchat.should_speak(self.GID, now=self.later(20, now), dt=now)
        self.assertFalse(ok)
        self.assertIn("没人接话", why)
        # 过一阵再给一次机会，不会一路沉默到明天
        ok, _ = groupchat.should_speak(self.GID, now=self.later(40, now), dt=now)
        self.assertTrue(ok)

    def test_someone_replies_resets_ignored(self):
        now = self.real_now()
        settings.group_max_hour = 99
        settings.group_max_day = 99
        for t in ["一", "二", "三"]:
            groupchat.note_spoke(self.GID, t, slot=now)
        groupchat.note_reply(self.GID)
        ok, _ = groupchat.should_speak(self.GID, now=self.later(10, now), dt=now)
        self.assertTrue(ok)

    def test_groups_are_independent(self):
        groupchat.note_spoke(111, "在 A 群说", slot=self.real_now())
        ok, _ = groupchat.should_speak(222, dt=self.real_now())
        self.assertTrue(ok, "A 群的限流不该影响 B 群")


class MemoryTest(GroupChatBase):
    GID = 235069995

    def test_keeps_recent_messages(self):
        for i in range(30):
            groupchat.note_message(self.GID, i, f"友{i}", f"第{i}句")
        recent = groupchat.recent(self.GID, 10)
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[-1]["text"], "第29句")

    def test_keeps_bounded(self):
        for i in range(50):
            groupchat.note_message(self.GID, 1, "阿明", f"第{i}句")
        self.assertLessEqual(len(groupchat.recent(self.GID, 100)), groupchat.KEEP_PER_GROUP)

    def test_blank_message_ignored(self):
        groupchat.note_message(self.GID, 1, "阿明", "   ")
        self.assertEqual(groupchat.recent(self.GID), [])


class ComposeTest(GroupChatBase):
    GID = 235069995

    class Fish:
        @staticmethod
        def system_prompt(session):
            return "你是大肥鱼。"

    class Brain:
        def __init__(self, out):
            self.out = out

        def complete(self, messages, temperature=0.5, max_tokens=200):
            self.seen = messages
            return self.out

    def test_nothing_to_say(self):
        self.assertIsNone(groupchat.compose(self.Fish(), self.Brain("（不说）"), self.GID, "在吗"))

    def test_says_short_thing(self):
        text = groupchat.compose(self.Fish(), self.Brain("「这比赛我压 BLG」"), self.GID, "今天谁强")
        self.assertEqual(text, "这比赛我压 BLG")

    def test_strips_name_prefix(self):
        text = groupchat.compose(self.Fish(), self.Brain("鲸鲸：我也觉得"), self.GID, "谁强")
        self.assertEqual(text, "我也觉得")

    def test_prompt_contains_group_context(self):
        groupchat.note_message(self.GID, 1, "阿明", "今晚看比赛吗")
        brain = self.Brain("（不说）")
        groupchat.compose(self.Fish(), brain, self.GID, "看啊")
        prompt = brain.seen[-1]["content"]
        self.assertIn("阿明", prompt)
        # 别再写「大多数时候你不该接话」那种话：模型会照做，群里就永远没声
        self.assertIn("想接就接一句", prompt)
        self.assertNotIn("能不说就不说", prompt)

    def test_chime_prompt_is_unprompted(self):
        groupchat.note_message(self.GID, 1, "阿明", "今晚看比赛吗")
        brain = self.Brain("（不说）")
        groupchat.compose(self.Fish(), brain, self.GID, mode="chime")
        prompt = brain.seen[-1]["content"]
        self.assertIn("群里还在聊", prompt)
        self.assertIn("突然插嘴", prompt)


class ParseTest(unittest.TestCase):
    """parse 不再把没 @ 的群消息丢掉，而是打个标记交出去。"""

    @staticmethod
    def payload(**kw) -> dict:
        base = {"post_type": "message", "message_type": "group", "group_id": 235069995,
                "user_id": 111, "self_id": 3877365065,
                "sender": {"card": "阿明", "nickname": "小明"},
                "message": [{"type": "text", "data": {"text": "今晚看比赛吗"}}]}
        base.update(kw)
        return base

    def test_unmentioned_group_message_is_returned(self):
        event = qq.parse(self.payload())
        self.assertIsNotNone(event)
        self.assertFalse(event["mentioned"])
        self.assertEqual(event["group_id"], 235069995)
        self.assertEqual(event["name"], "阿明")

    def test_mentioned_group_message(self):
        msg = [{"type": "at", "data": {"qq": "3877365065"}},
               {"type": "text", "data": {"text": " 在吗"}}]
        event = qq.parse(self.payload(message=msg))
        self.assertTrue(event["mentioned"])
        self.assertEqual(event["text"], "在吗")

    def test_own_message_ignored(self):
        self.assertIsNone(qq.parse(self.payload(user_id=3877365065)))

    def test_private_message(self):
        event = qq.parse(self.payload(message_type="private", user_id=2953133421))
        self.assertFalse(event["mentioned"])
        self.assertIsNone(event["group_id"])
        self.assertEqual(event["session"], "qq:private:2953133421")


class ChimeTest(GroupChatBase):
    """群里聊着的时候，她自己插一句——这是「自由」的部分，不是等别人点名。"""

    GID = 235069995

    def data(self, msg_ago: float = 60, chime_in: float = -1, **kw) -> dict:
        now = time.time()
        g = {
            "recent": [{"user_id": "1", "name": "阿明", "text": "今晚看比赛吗",
                        "ts": now - msg_ago}],
            "ignored": 0, "today": 0, "hour_count": 0,
            "day": datetime.now().strftime("%Y-%m-%d"),
            "chime_at": now + chime_in,
        }
        g.update(kw)
        return {"groups": {str(self.GID): g}}

    def test_chimes_when_group_is_warm(self):
        ok, why = groupchat.chime_due(self.GID, data=self.data(), dt=self.real_now())
        self.assertTrue(ok, why)
        self.assertIn("正聊着", why)

    def test_waits_for_planned_moment(self):
        ok, why = groupchat.chime_due(self.GID, data=self.data(chime_in=120), dt=self.real_now())
        self.assertFalse(ok)
        self.assertIn("再等等", why)

    def test_quiet_group_gets_no_chime(self):
        ok, why = groupchat.chime_due(self.GID, data=self.data(msg_ago=3600), dt=self.real_now())
        self.assertFalse(ok)
        self.assertIn("没动静", why)

    def test_respects_rate_limits(self):
        now = self.real_now()
        # 刚说过一句：间隔没到，冒泡也得等
        ok, why = groupchat.chime_due(self.GID, data=self.data(last_spoke=time.time() - 10), dt=now)
        self.assertFalse(ok)
        self.assertIn("刚说", why)

    def test_off_switch(self):
        settings.group_chime = False
        ok, why = groupchat.chime_due(self.GID, data=self.data(), dt=self.real_now())
        self.assertFalse(ok)
        self.assertIn("关着", why)

    def test_plan_is_stable_and_reset_after_speaking(self):
        first = groupchat._chime_target(self.GID)
        second = groupchat._chime_target(self.GID)
        self.assertAlmostEqual(first, second, places=3)
        groupchat.note_spoke(self.GID, "插一句", slot=self.real_now())
        self.assertIsNone(groupchat.state(self.GID).get("chime_at"))


class TickTest(GroupChatBase):
    """后台那一轮的接线：该冒泡的群要真的走一遍「说一句 + 记下来」。"""

    GID = 235069995

    class Fish:
        class mem:
            added = []

            @classmethod
            def add(cls, session, role, content):
                cls.added.append((session, role, content))
                return 1

        @staticmethod
        def system_prompt(session):
            return "你是大肥鱼。"

    def setUp(self):
        super().setUp()
        self.Fish.mem.added = []
        self._saved_speak = groupchat.speak_once
        self.calls = []

        def fake_speak(fish, brain, gid, incoming="", who="", force=False, mode="react"):
            self.calls.append((str(gid), mode))
            return {"sent": True, "text": "插一句"}

        groupchat.speak_once = fake_speak
        now = time.time()
        groupchat._write({"groups": {str(self.GID): {
            "recent": [{"user_id": "1", "name": "阿明", "text": "今晚看比赛吗", "ts": now - 30}],
            "ignored": 0, "today": 0, "hour_count": 0,
            "day": datetime.now().strftime("%Y-%m-%d"),
            "chime_at": now - 1,
        }}})

    def tearDown(self):
        groupchat.speak_once = self._saved_speak
        super().tearDown()

    def test_tick_chimes_and_records(self):
        spoke = groupchat.tick(self.Fish(), object())
        self.assertEqual(spoke, [str(self.GID)])
        self.assertEqual(self.calls, [(str(self.GID), "chime")])
        self.assertEqual(self.Fish.mem.added[0][0], f"qq:group:{self.GID}")
        # 她自己的话也进了群聊上下文
        self.assertEqual(groupchat.recent(self.GID)[-1]["text"], "插一句")

    def test_tick_skips_cold_group(self):
        now = time.time()
        groupchat._write({"groups": {str(self.GID): {
            "recent": [{"user_id": "1", "name": "阿明", "text": "老早的事了", "ts": now - 3600}],
            "ignored": 0, "chime_at": now - 1,
        }}})
        self.assertEqual(groupchat.tick(self.Fish(), object()), [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
