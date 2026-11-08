"""她的窗：日出日落、月相、天气。不联网也能算前三样。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app import sky

TZ = timezone(timedelta(hours=8))
SH = (31.23, 121.47)  # 上海


class SunTest(unittest.TestCase):
    def test_altitude_noon_vs_midnight(self):
        summer = datetime(2026, 6, 21, 12, 0, tzinfo=TZ)
        winter = datetime(2026, 12, 21, 12, 0, tzinfo=TZ)
        night = datetime(2026, 6, 21, 0, 30, tzinfo=TZ)
        self.assertGreater(sky.sun_altitude(summer, *SH), 75)
        self.assertLess(sky.sun_altitude(winter, *SH), 45)
        self.assertLess(sky.sun_altitude(night, *SH), 0)

    def test_sun_times_summer(self):
        sr, ss = sky.sun_times(datetime(2026, 6, 21).date(), *SH)
        self.assertIsNotNone(sr)
        self.assertIsNotNone(ss)
        self.assertLess(sr, ss)
        self.assertTrue(4 <= sr.hour <= 6, sr)
        self.assertTrue(18 <= ss.hour <= 20, ss)

    def test_sun_times_winter(self):
        sr, ss = sky.sun_times(datetime(2026, 12, 21).date(), *SH)
        self.assertTrue(6 <= sr.hour <= 8, sr)
        self.assertTrue(16 <= ss.hour <= 18, ss)

    def test_polar_night(self):
        # 北极圈里，冬至前后没有日出
        sr, ss = sky.sun_times(datetime(2026, 12, 21).date(), 78.0, 15.0)
        self.assertIsNone(sr)
        self.assertIsNone(ss)


class MoonTest(unittest.TestCase):
    def test_new_moon_epoch(self):
        name, illum = sky.moon_phase(datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc))
        self.assertEqual(name, "新月")
        self.assertLess(illum, 0.02)

    def test_full_moon(self):
        name, illum = sky.moon_phase(datetime(2000, 1, 21, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(name, "满月")
        self.assertGreater(illum, 0.95)

    def test_illumination_bounds(self):
        for h in range(0, 60):
            _name, illum = sky.moon_phase(
                datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=h)
            )
            self.assertGreaterEqual(illum, 0.0)
            self.assertLessEqual(illum, 1.0)


class TextTest(unittest.TestCase):
    def test_chinese_hour_edges(self):
        # 11 点、23 点以前会越界，整页的「她的窗」直接 500
        self.assertEqual(sky._cn_hour(11, 0), "上午十一点")
        self.assertEqual(sky._cn_hour(23, 5), "晚上十一点五分")
        self.assertEqual(sky._cn_hour(10, 0), "上午十点")

    def test_part_of_day(self):
        self.assertEqual(sky.part_of_day(datetime(2026, 6, 21, 2, 0, tzinfo=TZ), -30), "夜里")
        self.assertEqual(sky.part_of_day(datetime(2026, 6, 21, 19, 0, tzinfo=TZ), -23), "夜里")
        self.assertEqual(sky.part_of_day(datetime(2026, 6, 21, 12, 0, tzinfo=TZ), 80), "中午")
        self.assertEqual(sky.part_of_day(datetime(2026, 6, 21, 20, 0, tzinfo=TZ)), "晚上")

    def test_sky_text_without_place(self):
        # 没填城市：只说几点，不去联网
        original = sky.place
        sky.place = lambda: None
        try:
            text = sky.sky_text(datetime(2026, 6, 21, 20, 30, tzinfo=TZ))
        finally:
            sky.place = original
        self.assertTrue(text.endswith("。"))
        self.assertIn("晚上", text)

    def test_sky_text_with_place_offline(self):
        original_place, original_weather = sky.place, sky.weather
        sky.place = lambda: SH
        sky.weather = lambda *a, **k: None
        try:
            text = sky.sky_text(datetime(2026, 6, 21, 20, 30, tzinfo=TZ))
        finally:
            sky.place, sky.weather = original_place, original_weather
        self.assertIn("太阳", text)
        self.assertIn("月亮", text)


if __name__ == "__main__":
    unittest.main()
