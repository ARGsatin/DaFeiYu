"""不用真的挂 QQ，也能验证 /api/qq 这条链路通不通。

用法：先启动大肥鱼，另开一个终端：
    python tools/fake_qq.py 你好呀
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def main() -> None:
    text = " ".join(sys.argv[1:]) or "在吗，大肥鱼"
    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10000,
        "user_id": 12345,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
    }
    print(post("/api/qq", event))
    print("事件已投递。看大肥鱼终端日志，或查 data/fish.db 里的 qq:private:12345 会话。")
    print("注意：这里没有真的 NapCat 在跑，所以「把回复发回 QQ」这一步会失败，属正常。")


if __name__ == "__main__":
    main()
