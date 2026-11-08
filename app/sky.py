"""她的窗：知道外面天黑没黑、什么月亮、下没下雨。

太阳按经纬度现算（日出日落误差一两分钟），月相按日月黄经差算，天气查 Open-Meteo（免 Key，
一小时查一次，缓存到 data/sky.json）。这些只用来让她的话有「今天」，不参与任何决策，
所以整块都是「算不出来就算了」，绝不能因为它把聊天卡住。
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR, settings

RAD = math.pi / 180
DAY = 86400.0
CACHE_FILE = DATA_DIR / "sky.json"
WEATHER_TTL = 3600.0  # 天气一小时查一次
STALE_TTL = 6 * 3600.0  # 查不到就用 6 小时内的旧数据

# WMO 天气码 → 中文
WEATHER_CODES = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "打雷", 96: "雷阵雨带冰雹", 99: "雷阵雨带冰雹",
}


# ---------------- 太阳 ----------------
def _solar_terms(dt: datetime) -> tuple[float, float]:
    """返回 (时差分钟, 赤纬弧度)。用一年里的第几天算，够民用精度。"""
    utc = dt.astimezone(timezone.utc)
    n = utc.timetuple().tm_yday
    g = 2 * math.pi / 365.0 * (n - 1 + (utc.hour - 12) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    return eqtime, decl


def sun_altitude(dt: datetime, lat: float, lon: float) -> float:
    """太阳高度角（度）。负数就是在地平线以下。"""
    eqtime, decl = _solar_terms(dt)
    utc = dt.astimezone(timezone.utc)
    minutes = utc.hour * 60 + utc.minute + utc.second / 60.0
    solar_time = minutes + eqtime + 4 * lon
    hour_angle = (solar_time / 4.0 - 180.0) * RAD
    phi = lat * RAD
    alt = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, alt))))


def sun_times(day: date, lat: float, lon: float) -> tuple[datetime | None, datetime | None]:
    """日出、日落（本地时间）。极昼极夜返回 (None, None)。"""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=datetime.now().astimezone().tzinfo)
    eqtime, decl = _solar_terms(noon)
    phi = lat * RAD
    try:
        cos_ha = math.cos(90.833 * RAD) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    except ZeroDivisionError:
        return None, None
    if cos_ha > 1 or cos_ha < -1:
        return None, None
    ha = math.degrees(math.acos(cos_ha))

    def to_local(minutes_utc: float) -> datetime:
        base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return (base + timedelta(minutes=minutes_utc)).astimezone()

    return to_local(720 - 4 * (lon + ha) - eqtime), to_local(720 - 4 * (lon - ha) - eqtime)


# ---------------- 月亮 ----------------
SYNODIC = 29.530588853  # 朔望月（天）
MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
MOON_PHASES = [
    (1.85, "新月"), (5.54, "蛾眉月"), (9.23, "上弦月"), (12.91, "盈凸月"),
    (16.61, "满月"), (20.30, "亏凸月"), (23.99, "下弦月"), (27.68, "残月"),
    (float("inf"), "新月"),
]


def moon_phase(dt: datetime) -> tuple[str, float]:
    """返回（月相名, 被照亮比例 0~1）。"""
    age = ((dt - MOON_EPOCH).total_seconds() / DAY) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2
    for edge, name in MOON_PHASES:
        if age < edge:
            return name, illum
    return "新月", illum


# ---------------- 天气 ----------------
def _cache_read() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_write(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def weather(lat: float, lon: float, now: float | None = None) -> dict | None:
    """当前温度 + 天气。一小时内复用缓存；查不到就用 6 小时内的旧数据。"""
    now = time.time() if now is None else now
    cache = _cache_read()
    if (
        cache.get("weather")
        and abs(cache.get("lat", 0) - lat) < 0.2
        and abs(cache.get("lon", 0) - lon) < 0.2
        and now - cache.get("ts", 0) < WEATHER_TTL
    ):
        return cache["weather"]

    try:
        import requests

        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": round(lat, 3),
                "longitude": round(lon, 3),
                "current": "temperature_2m,weather_code,is_day",
                "timezone": "auto",
            },
            timeout=4,
        )
        r.raise_for_status()
        cur = (r.json() or {}).get("current") or {}
        data = {
            "temp": round(float(cur.get("temperature_2m")), 1),
            "code": int(cur.get("weather_code") or 0),
            "text": WEATHER_CODES.get(int(cur.get("weather_code") or 0), "说不清"),
            "is_day": bool(cur.get("is_day")),
        }
        _cache_write({"ts": now, "lat": lat, "lon": lon, "weather": data})
        return data
    except Exception:
        # 网络不通就用旧数据（最多 6 小时），再不行就只说日照
        stale = cache.get("weather")
        if stale and now - cache.get("ts", 0) < STALE_TTL:
            return stale
        return None


def geocode(name: str) -> tuple[float, float, str] | None:
    """城市名 → 经纬度。只在保存设置时调一次。"""
    name = (name or "").strip()
    if not name:
        return None
    try:
        import requests

        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "zh", "format": "json"},
            timeout=5,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("results") or []
        if not rows:
            return None
        row = rows[0]
        label = "·".join(
            str(x) for x in (row.get("country"), row.get("admin1"), row.get("name")) if x
        )
        return float(row["latitude"]), float(row["longitude"]), label
    except Exception:
        return None


# ---------------- 拼成一句话 ----------------
def _cn_num(n: int) -> str:
    """0~99 说成中文，34 → 三十四。"""
    d = "零一二三四五六七八九"
    if n < 10:
        return d[n]
    if n < 20:
        return "十" + (d[n % 10] if n % 10 else "")
    return d[n // 10] + "十" + (d[n % 10] if n % 10 else "")


def _cn_hour(h: int, m: int) -> str:
    if h == 0:
        head = "半夜"
    elif h < 5:
        head = f"凌晨{_cn_num(h)}点"
    elif h < 8:
        head = f"早上{_cn_num(h)}点"
    elif h < 12:
        head = f"上午{_cn_num(h)}点"
    elif h == 12:
        head = "中午十二点"
    elif h < 18:
        head = f"下午{_cn_num(h - 12)}点"
    else:
        head = f"晚上{_cn_num(h - 12)}点"
    if m:
        head += f"{_cn_num(m)}分"
    return head


def part_of_day(dt: datetime, alt: float | None = None) -> str:
    h = dt.hour
    if alt is not None:
        if alt < -12:
            return "夜里"
        if alt < -0.833:
            return "天已经黑透了" if h >= 15 else "天还没亮"
        if alt < 6:
            return "天刚亮" if h < 12 else "太阳快落了"
    if h < 5:
        return "夜里"
    if h < 9:
        return "早上"
    if h < 12:
        return "上午"
    if h < 14:
        return "中午"
    if h < 18:
        return "下午"
    return "晚上"


def place() -> tuple[float, float] | None:
    """她的窗在哪儿：.env 里填了坐标就用坐标，只填城市名就先查一次。"""
    if settings.sky_lat and settings.sky_lon:
        return settings.sky_lat, settings.sky_lon
    if settings.sky_city:
        hit = geocode(settings.sky_city)
        if hit:
            lat, lon, label = hit
            settings.sky_lat, settings.sky_lon = lat, lon
            _cache_write({**_cache_read(), "place": {"lat": lat, "lon": lon, "label": label}})
            return lat, lon
    return None


def sky_text(now: datetime | None = None) -> str:
    """一句话：几点、天什么样、天气、月亮。没填地点就只知道几点。"""
    now = now or datetime.now().astimezone()
    at = place()
    alt = sun_altitude(now, *at) if at else None
    head = f"现在是{_cn_hour(now.hour, now.minute)}"
    if not at:
        return head + "。"
    head += "，" + part_of_day(now, alt)
    bits = [head]
    lat, lon = at
    try:
        sunrise, sunset = sun_times(now.date(), lat, lon)
        if sunrise and sunset:
            if now < sunrise:
                bits.append(f"太阳 {sunrise.strftime('%H:%M')} 才出来")
            elif now < sunset:
                left = (sunset - now).total_seconds() / 3600
                bits.append(f"离天黑还有 {max(1, round(left))} 个小时")
            else:
                bits.append(f"太阳 {sunset.strftime('%H:%M')} 就落了")
    except Exception:
        pass

    w = weather(lat, lon)
    if w:
        bits.append(f"外面 {w['temp']:g} 度，{w['text']}")

    try:
        name, illum = moon_phase(now)
        if illum >= 0.85:
            bits.append(f"月亮是{name}，很亮")
        elif illum >= 0.35:
            bits.append(f"月亮是{name}")
        elif alt is not None and alt < 0:
            bits.append(f"月亮是{name}，几乎看不见")
    except Exception:
        pass

    return "，".join(bits) + "。"


def snapshot(now: datetime | None = None) -> dict:
    """给面板看的明细。"""
    now = now or datetime.now().astimezone()
    at = place()
    data: dict = {"text": sky_text(now), "at": None}
    if not at:
        return data
    lat, lon = at
    sunrise, sunset = sun_times(now.date(), lat, lon)
    name, illum = moon_phase(now)
    data.update(
        {
            "at": {"lat": lat, "lon": lon},
            "sun": {
                "altitude": round(sun_altitude(now, lat, lon), 1),
                "sunrise": sunrise.strftime("%H:%M") if sunrise else None,
                "sunset": sunset.strftime("%H:%M") if sunset else None,
            },
            "moon": {"name": name, "illumination": round(illum, 2)},
            "weather": weather(lat, lon),
        }
    )
    return data
