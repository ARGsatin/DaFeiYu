"""本地模型：从 ModelScope 下载的 Qwen，用 transformers 直接在显卡上跑。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator


class LocalModel:
    """懒加载 + 单实例。第一次调用会花十几秒把权重读进显存。"""

    def __init__(self, path: str | Path, max_new_tokens: int = 400):
        self.path = str(path)
        self.max_new_tokens = max_new_tokens
        self._model = None
        self._tok = None
        self._lock = threading.Lock()
        self.device = "?"

    # ---------- 加载 ----------
    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            print(f"[model] 正在加载 {self.path} …")
            self._tok = AutoTokenizer.from_pretrained(self.path, trust_remote_code=True)

            kwargs = {"trust_remote_code": True, "low_cpu_mem_usage": True}

            def load_with(dtype, **extra):
                # transformers v5 把 torch_dtype 改名成 dtype，两边都试一下
                try:
                    return AutoModelForCausalLM.from_pretrained(
                        self.path, dtype=dtype, **kwargs, **extra
                    )
                except TypeError:
                    return AutoModelForCausalLM.from_pretrained(
                        self.path, torch_dtype=dtype, **kwargs, **extra
                    )

            try:
                if not torch.cuda.is_available():
                    raise RuntimeError("没有可用的 CUDA")
                self._model = load_with(torch.float16, device_map="cuda")
                self.device = "cuda"
            except Exception as exc:
                print(f"[model] 显卡加载失败（{exc}），退回 CPU（会慢很多）")
                self._model = load_with(torch.float32)
                self._model.to("cpu")
                self.device = "cpu"

            self._model.eval()
            print(f"[model] 就绪，运行在 {self.device}")

    # ---------- 生成 ----------
    def stream(self, messages: list[dict], temperature: float = 0.85,
               max_tokens: int | None = None) -> Iterator[str]:
        self.load()
        import torch
        from transformers import TextIteratorStreamer

        prompt = self._tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._tok(prompt, return_tensors="pt").to(self._model.device)
        streamer = TextIteratorStreamer(
            self._tok, skip_prompt=True, skip_special_tokens=True, timeout=120
        )
        gen_kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=max_tokens or self.max_new_tokens,
            do_sample=temperature > 0,
            temperature=max(0.01, temperature),
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=self._tok.eos_token_id,
        )
        error: list[BaseException] = []

        def run() -> None:
            try:
                with torch.no_grad():
                    self._model.generate(**gen_kwargs)
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)
                streamer.end()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            for piece in streamer:
                if piece:
                    yield piece
        finally:
            thread.join(timeout=5)
        if error:
            yield f"\n(咕噜…我卡住了：{type(error[0]).__name__})"

    def complete(self, messages: list[dict], temperature: float = 0.4,
                 max_tokens: int = 300) -> str:
        return "".join(self.stream(messages, temperature, max_tokens)).strip()
