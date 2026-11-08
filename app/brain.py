"""大肥鱼的脑子：本地 transformers 模型 → Ollama → OpenAI 兼容 API → 离线兜底。"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Iterator

import requests

from .config import ROOT, Settings


class Brain:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.kind = "offline"
        self.model = ""
        self._client = None
        self._local = None
        self._detect()

    # ---------- 探测 ----------
    def _ollama_alive(self) -> bool:
        try:
            r = requests.get(self.cfg.ollama_base.rstrip("/") + "/models", timeout=1.2)
            if r.status_code != 200:
                return False
            data = r.json().get("data") or []
            names = [d.get("id", "") for d in data]
            if not names:
                return False
            want = self.cfg.ollama_model
            if any(n == want or n.startswith(want.split(":")[0]) for n in names):
                return True
            # 模型名对不上就用第一个已下载的
            self.cfg.ollama_model = names[0]
            return True
        except Exception:
            return False

    def _find_local_model(self) -> Path | None:
        if self.cfg.hf_model_dir:
            p = Path(self.cfg.hf_model_dir)
            return p if (p / "config.json").exists() else None
        base = ROOT / "models"
        if not base.is_dir():
            return None
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "config.json").exists():
                return d
        return None

    def _detect(self) -> None:
        from openai import OpenAI

        want = (self.cfg.provider or "auto").lower()

        def use_local() -> bool:
            path = self._find_local_model()
            if path is None:
                return False
            from .local_model import LocalModel

            self.kind = "hf"
            self.model = path.name
            self._local = LocalModel(path)
            return True

        def use_ollama() -> bool:
            if not self._ollama_alive():
                return False
            self.kind = "ollama"
            self.model = self.cfg.ollama_model
            self._client = OpenAI(base_url=self.cfg.ollama_base, api_key="ollama", timeout=120)
            return True

        def use_api() -> bool:
            if not self.cfg.api_key:
                return False
            self.kind = "api"
            self.model = self.cfg.api_model
            self._client = OpenAI(base_url=self.cfg.api_base, api_key=self.cfg.api_key, timeout=120)
            return True

        plans = {
            "auto": (use_local, use_ollama, use_api),
            "hf": (use_local,),
            "local": (use_local,),
            "transformers": (use_local,),
            "ollama": (use_ollama,),
            # 点名要在线 API；万一没填 key，先拿本地模型顶着，别变成哑巴鱼
            "api": (use_api, use_local, use_ollama),
        }
        plan = plans.get(want, plans["auto"])

        for step in plan:
            if step():
                if want == "api" and self.kind != "api":
                    print("[model] FISH_PROVIDER=api 但没读到 API_KEY，先用本地模型顶着")
                return
        self.kind = "offline"

    def reload(self) -> dict:
        """面板上换了模型 / 服务商之后，重新找一次脑子，不用重启整个大肥鱼。"""
        self.kind = "offline"
        self.model = ""
        self._client = None
        self._local = None
        self._detect()
        return self.describe()

    def describe(self) -> dict:
        labels = {
            "hf": f"本地 {self.model}",
            "ollama": f"本地模型 {self.model}",
            "api": f"在线模型 {self.model}",
            "offline": "离线占位（还没接上脑子）",
        }
        return {"kind": self.kind, "model": self.model, "label": labels[self.kind]}

    def warmup(self) -> None:
        """启动时提前把权重读进显存，免得第一句话等太久。"""
        if self.kind == "hf" and self._local is not None:
            try:
                self._local.load()
            except Exception as exc:  # noqa: BLE001
                print(f"[model] 预加载失败：{exc}")

    # ---------- 生成 ----------
    def stream(self, messages: list[dict], temperature: float = 0.85,
               max_tokens: int = 400) -> Iterator[str]:
        if self.kind == "offline":
            yield from self._offline(messages)
            return
        if self.kind == "hf":
            yield from self._local.stream(messages, temperature, max_tokens)
            return
        try:
            kwargs = dict(model=self.model, messages=messages,
                          temperature=temperature, max_tokens=max_tokens, stream=True)
            if self.kind == "ollama":
                kwargs["extra_body"] = {"options": {"num_ctx": 4096}}
            for chunk in self._client.chat.completions.create(**kwargs):
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content
                if piece:
                    yield piece
        except Exception as exc:  # 网络抖动、模型没起来
            if any(isinstance(m.get("content"), list) for m in messages):
                yield "\n(咕噜…这张图我看不清，现在这个模型好像不认图片。)"
                return
            yield f"\n(咕噜…我脑子卡住了：{type(exc).__name__}。要不要看看模型服务还开着没？)"

    def complete(self, messages: list[dict], temperature: float = 0.4,
                 max_tokens: int = 300) -> str:
        if self.kind == "offline":
            return "".join(self._offline(messages))
        if self.kind == "hf":
            return self._local.complete(messages, temperature, max_tokens)
        try:
            resp = self._client.chat.completions.create(
                model=self.model, messages=messages,
                temperature=temperature, max_tokens=max_tokens, stream=False,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return ""

    # ---------- 整理长期记忆 ----------
    def summarize(self, transcript: str, previous: str) -> str:
        if self.kind == "offline":
            return ""
        prompt = (
            "把下面这段对话压缩成 5 句以内的中文笔记，只保留：主人是谁、聊过什么、"
            "主人的偏好或近况、大肥鱼答应过什么。不要评价，不要客套。\n\n"
            f"【之前的笔记】\n{previous or '（无）'}\n\n【新对话】\n{transcript}\n\n【合并后的笔记】"
        )
        return self.complete([{"role": "user", "content": prompt}],
                             temperature=0.2, max_tokens=260)

    # ---------- 整理记忆流 ----------
    def rewrite_memories(self, entries: list[dict]) -> dict[str, str]:
        """一批快忘的记忆，一次调用改写成「记不太清」的样子。返回 {id: 新正文}。

        批量是为了省钱：十次单条调用不如一次。
        """
        if self.kind == "offline" or not entries:
            return {}
        rows = entries[:10]
        lines = "\n".join(f"{i + 1}. {e.get('text', '')}" for i, e in enumerate(rows))
        prompt = (
            "下面是大肥鱼（一条住在电脑里的小姑娘鱼）快忘掉的几件事。"
            "请逐条改写成「记不太清」的样子：大概什么事、什么感觉还在；"
            "人名、标题、地点、数字都用「好像」「某个」带过，细节别留。"
            "每条不超过 40 字，用第一人称。\n"
            "严格按原编号逐行输出，不要解释，像这样：\n1. 记不太清了，好像……\n\n"
            f"{lines}\n\n改写："
        )
        out = self.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=80 * len(rows),
        )
        result: dict[str, str] = {}
        for line in (out or "").splitlines():
            hit = re.match(r"\s*(\d+)\s*[.、)）]\s*(.+)", line)
            if not hit:
                continue
            idx = int(hit.group(1)) - 1
            if 0 <= idx < len(rows):
                text = hit.group(2).strip().strip('"“”')[:80]
                if text:
                    result[rows[idx]["id"]] = text
        return result

    def merge_memories(self, group: list[dict]) -> str | None:
        """几天前差不多的几件事，概括成一条「印象」。"""
        if self.kind == "offline":
            return None
        lines = "\n".join(f"- {e.get('text', '')}" for e in group[:8])
        prompt = (
            "下面是大肥鱼（一条住在电脑里的小姑娘鱼）这几天记下的几件事，看着差不多是一回事。"
            "请用第一人称把它们概括成一句「印象」：留下最有画面感的细节，不超过 50 字，"
            "不分点、不评价、不写成总结。只输出那一句。\n\n"
            f"{lines}\n\n印象："
        )
        out = self.complete([{"role": "user", "content": prompt}],
                            temperature=0.6, max_tokens=100)
        out = (out or "").strip().strip('"“”').strip()
        return out[:120] or None

    # ---------- 没接上模型时的兜底 ----------
    def _offline(self, messages: list[dict]) -> Iterator[str]:
        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last = m.get("content", "")
                break
        replies = [
            "咕噜……我脑子还没接上电，现在只会吐泡泡。主人先把模型开起来好不好？",
            "唔，我现在是个空壳鱼。你去看一眼 README，把 Ollama 或者 API 填上，我就活了。",
            "（鱼嘴一张一合）……我知道你在说话，但我这会儿真的想不起来怎么回。",
        ]
        if any(w in last for w in ("饿", "吃", "喂")):
            replies.append("说到吃的我就来劲了——可惜我现在连饭盆都端不动，脑子还没装好。")
        text = random.choice(replies)
        for ch in text:
            time.sleep(0.012)
            yield ch
