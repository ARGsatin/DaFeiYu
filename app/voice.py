"""大肥鱼的嘴和耳朵：edge-tts 说话（不通就退回 Windows 自带语音），faster-whisper 听话。"""
from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import requests

from .config import Settings, settings


def _strip_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"[*_#>`~]", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # 括号里的「尾巴摇摇」是给她自己看的，念出来很怪
    text = re.sub(r"[（(][^）)]{0,16}[）)]", "", text)
    # emoji 别念成「笑脸」之类
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _prosody(value: str, unit: str, default: str) -> str:
    """把 .env 里写的 25Hz / 6% / +6% 统一成 edge-tts 要的带符号格式。"""
    v = (value or "").strip()
    if not v:
        return default
    if v[0] not in "+-":
        v = "+" + v
    return v if v.endswith(unit) else v + unit


def _pitch_shift_wav(data: bytes, factor: float) -> bytes | None:
    """把 16-bit PCM WAV 的时长拉长、音调压低（factor<1）。占位用，不追求发烧。"""
    try:
        import numpy as np

        with wave.open(io.BytesIO(data), "rb") as src:
            channels = src.getnchannels()
            width = src.getsampwidth()
            rate = src.getframerate()
            frames = src.getnframes()
            raw = src.readframes(frames)
        if width != 2 or not raw:
            return None

        x = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            x = x.reshape(-1, channels)
        n = max(1, int(len(x) / max(0.5, min(1.0, factor))))
        idx = np.linspace(0, len(x) - 1, n)

        if channels > 1:
            y = np.stack(
                [np.interp(idx, np.arange(len(x)), x[:, c]) for c in range(channels)],
                axis=1,
            )
        else:
            y = np.interp(idx, np.arange(len(x)), x)
        y = np.clip(y, -32768, 32767).astype(np.int16)

        out = io.BytesIO()
        with wave.open(out, "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(width)
            dst.setframerate(rate)
            dst.writeframes(y.tobytes())
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        print(f"[voice] 降音调失败，先发原声：{exc}")
        return None


def _normalize_wav(data: bytes, target_dbfs: float = -18.0,
                   peak_dbfs: float = -1.0) -> bytes | None:
    """把 WAV 的响度拉到目标 RMS；Breeze 同一条文本两次生成的音量会差不少。"""
    try:
        import numpy as np

        with wave.open(io.BytesIO(data), "rb") as src:
            channels = src.getnchannels()
            width = src.getsampwidth()
            rate = src.getframerate()
            raw = src.readframes(src.getnframes())
        if width != 2 or not raw:
            return None

        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.max(np.abs(x)))
        if rms < 1e-6 or peak < 1e-6:
            return None

        gain = 10 ** ((target_dbfs - 20 * np.log10(rms)) / 20)
        # 别把峰值顶到 0 dBFS，留一点余量
        ceiling = 10 ** ((peak_dbfs - 20 * np.log10(peak)) / 20)
        gain = max(0.1, min(8.0, min(gain, ceiling)))

        y = np.clip(x * gain, -1.0, 1.0)
        y = (y * 32767).astype(np.int16)
        out = io.BytesIO()
        with wave.open(out, "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(width)
            dst.setframerate(rate)
            dst.writeframes(y.tobytes())
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        print(f"[voice] 响度归一化失败，先发原声：{exc}")
        return None


def _patch_av_open() -> None:
    """PyAV 19 去掉了 metadata_errors 参数，而 faster-whisper 还在传，这里兜一层。"""
    try:
        import av

        original = av.open
        if getattr(original, "_fatfish_compat", False):
            return

        def compat_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return original(*args, **kwargs)

        compat_open._fatfish_compat = True
        av.open = compat_open
    except Exception:
        pass


_patch_av_open()


class Voice:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._stt = None
        self._stt_lock = threading.Lock()
        self._stt_broken = False
        self._edge_broken = False
        self._breeze_broken = False
        # 上一次「说」用的是哪个引擎：breeze / edge / sapi，面板试听时显示用
        self.last_engine = ""
        if self.cfg.tts_provider == "breeze" and not self.cfg.breeze_api_key:
            print("[voice] FISH_TTS=breeze 但 BREEZE_API_KEY 是空的，先用 edge-tts")

    def reset(self, stt: bool = False) -> None:
        """设置改过之后清掉「坏过一次就别再试」的标记。

        stt 只在换了听写模型时才清：不然每次保存设置都要重新加载一遍 whisper。
        """
        if stt:
            self._stt = None
            self._stt_broken = False
        self._edge_broken = False
        self._breeze_broken = False

    # ---------- 说 ----------
    async def tts(self, text: str) -> tuple[bytes, str] | None:
        text = _strip_for_speech(text)
        if not text:
            return None

        self.last_engine = ""
        # BreezeBlue：博客里那个「迷糊版」音色。没配 key / 失败就往下走 edge-tts
        if (self.cfg.tts_provider == "breeze" and self.cfg.breeze_api_key
                and not self._breeze_broken):
            breeze = await asyncio.to_thread(self._breeze, text)
            if breeze:
                self.last_engine = "breeze"
                return breeze

        audio = await self._edge(text)
        if audio:
            self.last_engine = "edge"
            return audio, "audio/mpeg"

        audio = await asyncio.to_thread(self._sapi, text)
        if audio:
            self.last_engine = "sapi"
            return audio, "audio/wav"
        return None

    def _breeze(self, text: str) -> tuple[bytes, str] | None:
        """BreezeBlue 的同步合成接口，返回（音频字节, mime）。"""
        key = self.cfg.breeze_api_key
        if not key:
            return None
        url = f"{self.cfg.breeze_api_base}/text-to-speech/{self.cfg.breeze_voice_id}"
        body: dict = {"text": text[:1000]}
        if self.cfg.breeze_language:
            body["language_code"] = self.cfg.breeze_language
        if self.cfg.breeze_model_id:
            body["model_id"] = self.cfg.breeze_model_id
        if self.cfg.breeze_instructions:
            body["instructions"] = self.cfg.breeze_instructions

        voice_settings: dict = {}
        if abs(self.cfg.breeze_speed - 1.0) > 1e-3:
            voice_settings["speed"] = max(0.5, min(2.0, self.cfg.breeze_speed))
        if abs(self.cfg.breeze_guidance - 1.0) > 1e-3:
            voice_settings["guidance_scale"] = max(1.0, min(10.0, self.cfg.breeze_guidance))
        if voice_settings:
            body["voice_settings"] = voice_settings

        # 需要后期处理（降调 / 拉响度）时拿 WAV；否则直接拿更小的 mp3
        pitch = max(0.5, min(1.0, self.cfg.breeze_pitch))
        normalize = self.cfg.breeze_normalize
        want_wav = abs(pitch - 1.0) > 1e-3 or normalize
        output_format = "wav_24000" if want_wav else "mp3"
        try:
            r = requests.post(
                url,
                params={"output_format": output_format},
                json=body,
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                timeout=90,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] Breeze 连不上：{type(exc).__name__}")
            return None

        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code == 200 and len(r.content) > 800 and (
                "audio" in ctype or r.content[:3] == b"ID3"):
            if not want_wav:
                return r.content, "audio/mpeg"
            if r.content[:4] != b"RIFF":
                # 服务端没按 wav 返，就别硬套 WAV 的头
                return r.content, "audio/mpeg"
            data = r.content
            if abs(pitch - 1.0) > 1e-3:
                data = _pitch_shift_wav(data, pitch) or data
            if normalize:
                data = _normalize_wav(
                    data, self.cfg.breeze_target_dbfs, self.cfg.breeze_peak_dbfs
                ) or data
            return data, "audio/wav"

        # 错误里别把 key 打出来；只留一小段，方便看是 401 还是额度/参数问题
        detail = re.sub(r"brz_[A-Za-z0-9]+", "brz_***", r.text[:200]).replace("\n", " ")
        print(f"[voice] Breeze 合成失败（{r.status_code}）：{detail}")
        if r.status_code in (401, 403):
            # key 不对就别每条都撞一次；改完 .env 重启即可
            self._breeze_broken = True
        return None

    def tts_sync(self, text: str) -> tuple[bytes, str] | None:
        """给 QQ 发送这种同步路径用：在后台线程里跑一遍 tts()。"""
        try:
            return asyncio.run(self.tts(text))
        except RuntimeError:
            # 调用方自己就在事件循环里，扔到另一个线程，别把循环堵死
            box: dict = {}

            def worker() -> None:
                try:
                    box["value"] = asyncio.run(self.tts(text))
                except Exception as exc:  # noqa: BLE001
                    print(f"[voice] 生成语音失败：{exc}")

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout=60)
            return box.get("value")
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] 生成语音失败：{exc}")
            return None

    async def _edge(self, text: str) -> bytes | None:
        if self._edge_broken:
            return None
        try:
            import edge_tts

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
                path = Path(fh.name)
            try:
                communicate = edge_tts.Communicate(
                    text,
                    self.cfg.voice,
                    rate=_prosody(self.cfg.voice_rate, "%", "+0%"),
                    pitch=_prosody(self.cfg.voice_pitch, "Hz", "+0Hz"),
                )
                await communicate.save(str(path))
                data = path.read_bytes()
                return data if len(data) > 800 else None
            finally:
                path.unlink(missing_ok=True)
        except Exception as exc:  # 国内经常 403，直接切本地
            print(f"[voice] edge-tts 不可用（{type(exc).__name__}），改用系统语音")
            self._edge_broken = True
            return None

    def _sapi(self, text: str) -> bytes | None:
        """Windows 自带 TTS，离线、机械，但一定有。"""
        try:
            with tempfile.TemporaryDirectory() as tmp:
                txt = Path(tmp) / "say.txt"
                wav = Path(tmp) / "say.wav"
                txt.write_text(text, encoding="utf-8")
                script = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath '{txt}' -Raw -Encoding UTF8
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$zh = $synth.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh*' -and $_.VoiceInfo.Name -like '*Yaoyao*' }} | Select-Object -First 1
if (-not $zh) {{ $zh = $synth.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh*' }} | Select-Object -First 1 }}
if ($zh) {{ $synth.SelectVoice($zh.VoiceInfo.Name) }}
$synth.Rate = 1
$synth.SetOutputToWaveFile('{wav}')
$synth.Speak($text)
$synth.Dispose()
"""
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-Command", script],
                    capture_output=True, timeout=40,
                    # 窗口程序里起控制台程序会闪一下黑框，藏掉
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if wav.exists() and wav.stat().st_size > 2000:
                    return wav.read_bytes()
        except Exception as exc:
            print(f"[voice] 系统语音也失败了：{exc}")
        return None

    # ---------- 听 ----------
    def _load_stt(self):
        if self._stt is not None or self._stt_broken:
            return self._stt
        with self._stt_lock:
            if self._stt is not None or self._stt_broken:
                return self._stt
            try:
                from faster_whisper import WhisperModel

                candidates = [("cpu", "int8")]
                try:
                    import torch

                    if torch.cuda.is_available():
                        candidates.insert(0, ("cuda", "float16"))
                except Exception:
                    pass

                for device, compute in candidates:
                    try:
                        print(f"[voice] 正在加载语音识别模型 {self.cfg.stt_model} ({device})…")
                        self._stt = WhisperModel(
                            self.cfg.stt_model, device=device, compute_type=compute
                        )
                        print(f"[voice] 语音识别就绪（{device}）")
                        break
                    except Exception as exc:
                        print(f"[voice] {device} 加载失败：{exc}")
                if self._stt is None:
                    self._stt_broken = True
            except Exception as exc:
                print(f"[voice] 语音识别不可用：{exc}")
                self._stt_broken = True
            return self._stt

    def stt(self, audio: bytes, suffix: str = ".webm") -> str:
        model = self._load_stt()
        if model is None:
            return ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(audio)
            path = Path(fh.name)
        try:
            segments, _info = model.transcribe(
                str(path),
                language="zh",
                vad_filter=True,
                beam_size=5,
                initial_prompt="以下是普通话对话，请用简体中文转写。",
            )
            return "".join(s.text for s in segments).strip()
        except Exception as exc:
            print(f"[voice] 识别失败：{exc}")
            return ""
        finally:
            path.unlink(missing_ok=True)

    def stt_async(self, audio: bytes) -> str:
        return asyncio.to_thread(self.stt, audio)


_shared: Voice | None = None


def shared() -> Voice:
    """全局一个就行：edge-tts 挂掉之后不用每次重试，STT 也只加载一次。"""
    global _shared
    if _shared is None:
        _shared = Voice(settings)
    return _shared
