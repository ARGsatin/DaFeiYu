"""BreezeBlue 合成请求的形状，以及失败时回退 edge-tts。"""
from __future__ import annotations

import asyncio
import io
import unittest
import wave

import numpy as np

from app import voice as voice_mod
from app.config import Settings
from app.voice import Voice


def make_wav(seconds: float = 1.0, rate: int = 24000,
             amp: float = 0.0) -> bytes:
    frames = int(rate * seconds)
    if amp:
        t = np.arange(frames) / rate
        samples = (amp * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    else:
        samples = np.zeros(frames, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"",
                 ctype: str = "audio/mpeg", text: str = ""):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}
        self.text = text


class BreezeRequestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        self.cfg.tts_provider = "breeze"
        self.cfg.breeze_api_key = "brz_test_key"
        self.cfg.breeze_voice_id = "voc_8yud7hz7c9q2"
        self.cfg.breeze_model_id = "breeze-tts-2"
        self.cfg.breeze_language = "zh"
        self.cfg.breeze_instructions = ""
        self.cfg.breeze_speed = 1.0
        self.cfg.breeze_guidance = 1.0
        self.cfg.breeze_pitch = 1.0
        self.cfg.breeze_normalize = False
        self.cfg.breeze_target_dbfs = -18.0
        self.cfg.breeze_peak_dbfs = -1.0
        self.calls = []
        self.old_post = voice_mod.requests.post

    def tearDown(self):
        voice_mod.requests.post = self.old_post

    def _fake_post(self, response: FakeResponse):
        def post(url, **kwargs):
            self.calls.append((url, kwargs))
            return response
        voice_mod.requests.post = post

    def test_request_shape(self):
        self._fake_post(FakeResponse(content=b"ID3" + b"x" * 2000))
        audio, mime = Voice(self.cfg)._breeze("你好呀")
        self.assertTrue(audio.startswith(b"ID3"))
        self.assertEqual(mime, "audio/mpeg")
        url, kwargs = self.calls[0]
        self.assertIn("/text-to-speech/voc_8yud7hz7c9q2", url)
        self.assertEqual(kwargs["params"], {"output_format": "mp3"})
        self.assertEqual(kwargs["headers"]["xi-api-key"], "brz_test_key")
        self.assertEqual(kwargs["json"]["text"], "你好呀")
        self.assertEqual(kwargs["json"]["language_code"], "zh")
        self.assertEqual(kwargs["json"]["model_id"], "breeze-tts-2")
        self.assertNotIn("voice_settings", kwargs["json"])

    def test_style_settings_and_pitch_shift(self):
        raw = make_wav(seconds=1.0)
        self._fake_post(FakeResponse(content=raw, ctype="audio/wav"))
        self.cfg.breeze_speed = 1.1
        self.cfg.breeze_guidance = 5.0
        self.cfg.breeze_pitch = 0.9
        audio, mime = Voice(self.cfg)._breeze("你好呀")
        self.assertEqual(mime, "audio/wav")
        _, kwargs = self.calls[0]
        self.assertEqual(kwargs["params"], {"output_format": "wav_24000"})
        self.assertEqual(kwargs["json"]["voice_settings"],
                         {"speed": 1.1, "guidance_scale": 5.0})
        with wave.open(io.BytesIO(audio), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        self.assertGreater(duration, 1.05)

    def test_normalize_lifts_quiet_audio(self):
        raw = make_wav(seconds=1.0, amp=0.05)
        self._fake_post(FakeResponse(content=raw, ctype="audio/wav"))
        self.cfg.breeze_normalize = True
        audio, mime = Voice(self.cfg)._breeze("你好呀")
        self.assertEqual(mime, "audio/wav")
        with wave.open(io.BytesIO(audio), "rb") as w:
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rms = float(np.sqrt(np.mean((data / 32768.0) ** 2)))
        dbfs = 20 * np.log10(rms)
        self.assertGreater(dbfs, -19.5)
        self.assertLess(dbfs, -16.5)

    def test_bad_key_marks_broken_and_falls_back(self):
        self._fake_post(FakeResponse(status=401, ctype="application/json",
                                     text='{"ok":false,"code":"invalid_api_key"}'))
        v = Voice(self.cfg)
        self.assertIsNone(v._breeze("你好呀"))
        self.assertTrue(v._breeze_broken)

    def test_json_error_is_not_audio(self):
        self._fake_post(FakeResponse(status=200, ctype="application/json",
                                     content=b'{"ok":false}', text="{}"))
        self.assertIsNone(Voice(self.cfg)._breeze("你好呀"))

    def test_tts_uses_breeze_first(self):
        v = Voice(self.cfg)
        v._breeze = lambda text: (b"ID3" + b"x" * 2000, "audio/mpeg")
        audio, mime = asyncio.run(v.tts("你好呀"))
        self.assertEqual(mime, "audio/mpeg")
        self.assertTrue(audio.startswith(b"ID3"))

    def test_tts_falls_back_to_edge_when_breeze_fails(self):
        v = Voice(self.cfg)
        v._breeze = lambda text: None

        async def fake_edge(text):
            return b"edge-audio"

        v._edge = fake_edge
        audio, mime = asyncio.run(v.tts("你好呀"))
        self.assertEqual(audio, b"edge-audio")
        self.assertEqual(mime, "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
