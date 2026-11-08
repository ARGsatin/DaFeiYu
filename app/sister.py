"""妹妹：第二条大肥鱼，另一个 QQ 号，另一个端口。

她是「同一套程序、另一份数据」——不是同一进程里开个小号：

- 数据在 `sister/`（自己的 `persona.md`、`fish.db`、`peer.json`），拉起来就是
  `python -m app` 加上 `FISH_HOME=sister/`，端口 8766，OneBot 走 3001；
- 两个人各记各的记忆，谁也不会把对方的聊天记成「主人说的」；
- 合在一起聊的时候，两边都走 `app.peer` 的刹车，不会没完没了。

这里负责：建好那份数据、把 `.env` 从主配置推导出来、拉起来 / 停掉 / 看状态。
面板和 `启动妹妹.bat` 都调这几个函数。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from . import peer
from .config import BUNDLE_DIR, ROOT, Settings, settings

PROFILE = ROOT / "sister"
PID_FILE = ROOT / "data" / "sister.pid"
LOG_DIR = ROOT / "data"
START_COOLDOWN = 8

# 妹妹那份 .env 里不能继承的东西：继承了会「妹妹再启动一个妹妹」或者抢主号
DROP_KEYS = {
    "FISH_SISTER", "FISH_SISTER_UIN", "FISH_SISTER_API", "FISH_SISTER_TOKEN",
    "FISH_SISTER_PORT", "FISH_SELF_UIN", "FISH_PEER_UIN", "FISH_PEER_OPEN",
    "FISH_HOST", "FISH_PORT", "FISH_SESSION", "FISH_NO_BROWSER",
    "FISH_STICKER_DIR", "NAPCAT_DIR", "FISH_SISTER_NAPCAT",
}

# 她一醒就带上的默认值：不主动找人、不插群、不安浏览器；只回姐姐的话
SISTER_ENV_DEFAULTS = {
    "FISH_HOST": "127.0.0.1",
    "FISH_SESSION": "sister",
    "FISH_NO_BROWSER": "1",
    "FISH_SISTER": "0",
    "FISH_PEER_OPEN": "0",
    "FISH_PEER_ONLY": "1",
    # 她记忆里管对面叫什么：她是妹妹，对面是姐姐
    "FISH_PEER_NAME": "姐姐",
    "FISH_PROACTIVE": "0",
    "FISH_SLOTS": "0",
    "FISH_FOLLOW": "0",
    "FISH_GROUP_CHAT": "0",
    "FISH_GROUP_AT_ONLY": "1",
    "FISH_GROUP_ALLOW": "",
    "FISH_STICKER_DIR": str(ROOT / "stickers"),
}


# ---------------------------------------------------------------- 那份数据
def url(cfg: Settings | None = None) -> str:
    cfg = cfg or settings
    host = (cfg.host or "127.0.0.1").strip() or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{cfg.sister_port}"


def napcat_dir(cfg: Settings | None = None) -> str:
    """妹妹用的那份 NapCat 在哪。

    主 .env 里的 NAPCAT_DIR 是**鲸鲸自己的**（D:\\NapCat）。妹妹那份必须另指一个目录：
    面板上的「启动 NapCat」会去跑那个目录里的 `startNapCat.ps1`，而鲸鲸那份的脚本
    开头就 `taskkill` 掉本机所有 QQ —— 跑错了等于把鲸鲸踢下线。
    留空就在鲸鲸那份旁边找 `NapCat2`。
    """
    cfg = cfg or settings
    raw = (getattr(cfg, "sister_napcat", "") or "").strip().strip('"')
    if raw:
        return raw
    main = (getattr(cfg, "napcat_dir", "") or "").strip().strip('"')
    base = Path(main).parent if main else Path("D:/NapCat").parent
    for name in ("NapCat2", "napcat2"):
        p = base / name
        try:
            if (p / "startNapCat.ps1").is_file() or (p / "NapCatWinBootMain.exe").is_file():
                return str(p)
        except OSError:
            continue
    return ""


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip().strip('"').strip("'")
    return out


def env_values(cfg: Settings | None = None, main_uin: str = "") -> dict[str, str]:
    """从主配置推导出妹妹那份 .env。纯函数，方便测。"""
    cfg = cfg or settings
    values = {k: v for k, v in _read_env(ROOT / ".env").items() if k not in DROP_KEYS}
    values.update(SISTER_ENV_DEFAULTS)
    values.update({
        "FISH_PORT": str(cfg.sister_port),
        "QQ_API": (cfg.sister_api or "").rstrip("/"),
        "QQ_TOKEN": cfg.sister_token,
    })
    if main_uin:
        values["FISH_PEER_UIN"] = main_uin
    # 面板上的「启动 NapCat」按这个目录找启动器：必须指向第二份，别指到鲸鲸那份
    nd = napcat_dir(cfg)
    if nd:
        values["NAPCAT_DIR"] = nd
    # 妹妹的网络、语音这些跟着主配置走；主动/群聊是上面写死的那几条
    return values


def _env_text(values: dict[str, str]) -> str:
    header = (
        "# 妹妹（鱼丸）的配置：由「大肥鱼」面板自动生成，改这里不会被记住。\n"
        "# 要调她，去主面板的「妹妹」页；要改她的脾气，去「人格」页的 sister/persona.md。\n"
        "# 这个文件每次启动妹妹都会重写一遍。\n\n"
    )
    body = "\n".join(f"{k}={v}" for k, v in values.items())
    return header + body + "\n"


def ensure_profile(cfg: Settings | None = None, main_uin: str = "") -> dict:
    """建好 sister/：目录、人格（没有才写）、.env（每次重写）。"""
    cfg = cfg or settings
    PROFILE.mkdir(parents=True, exist_ok=True)
    (PROFILE / "data").mkdir(parents=True, exist_ok=True)
    made: list[str] = []

    if not (PROFILE / "persona.md").exists():
        template = PROFILE / "persona.md"
        # 打包版：人格模板跟着 exe 一起走（_MEIPASS/sister/persona.md）
        bundled = BUNDLE_DIR / "sister" / "persona.md"
        if bundled.is_file():
            template.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            template.write_text(SISTER_PERSONA_FALLBACK, encoding="utf-8")
        made.append("persona.md")

    rules = PROFILE / "personas.json"
    if not rules.exists():
        rules.write_text(json.dumps({"default": "persona.md"}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        made.append("personas.json")

    envc = PROFILE / ".env"
    text = _env_text(env_values(cfg, main_uin))
    if not envc.exists() or envc.read_text(encoding="utf-8") != text:
        envc.write_text(text, encoding="utf-8")
        made.append(".env")

    (PROFILE / "stickers").mkdir(exist_ok=True)
    return {"dir": str(PROFILE), "made": made}


# ---------------------------------------------------------------- 谁是谁
def login_info(api: str, token: str = "", timeout: float = 2.0) -> dict:
    base = (api or "").rstrip("/")
    if not base:
        return {}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        data = requests.get(base + "/get_login_info", headers=headers, timeout=timeout).json()
    except Exception:  # noqa: BLE001
        return {}
    return data.get("data") or {}


def main_uin(cfg: Settings | None = None, timeout: float = 2.0) -> str:
    """鲸鲸自己的 QQ。先看配置，再问 NapCat——妹妹得知道「谁是姐姐」。"""
    cfg = cfg or settings
    if (cfg.self_uin or "").strip():
        return cfg.self_uin.strip()
    info = login_info(cfg.qq_api, cfg.qq_token, timeout=timeout)
    uin = str(info.get("user_id") or "")
    return "" if uin in ("", "0") else uin


def sister_uin(cfg: Settings | None = None, timeout: float = 2.0) -> str:
    cfg = cfg or settings
    if (cfg.sister_uin or "").strip():
        return cfg.sister_uin.strip()
    info = login_info(cfg.sister_api, cfg.sister_token, timeout=timeout)
    uin = str(info.get("user_id") or "")
    return "" if uin in ("", "0") else uin


# ---------------------------------------------------------------- 进程
def _pid() -> int:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return str(pid) in (out.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def healthy(cfg: Settings | None = None, timeout: float = 1.2) -> dict | None:
    """妹妹的网页服务通不通；通了就把 /api/health 还回来。"""
    try:
        r = requests.get(url(cfg) + "/api/health", timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def launch_command() -> list[str]:
    """拉妹妹那套的命令（和双击 `启动妹妹.bat` 是同一句）。

    打包成 exe 之后没有 `python -m app`，只能让 exe 自己带 `--serve` 再跑一份服务，
    和托盘拉起后台服务用的是同一条路。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve", "--parent", "0"]
    return [sys.executable, "-m", "app"]


def launch_env(cfg: Settings | None = None) -> dict[str, str]:
    """拉起妹妹时的环境变量。

    主进程 import config 的时候，已经把主号那一整份 .env 塞进了 os.environ。
    要是原样继承，妹妹会先读到这些变量，她自己那份 .env 就全被盖住了
    （结果就是妹妹也去连 3000、也开主动、也接群聊）。所以只留系统变量，
    配置一律以 sister/.env 为准；下面这几个是必须当场指定的。
    """
    cfg = cfg or settings
    managed = ("FISH_", "QQ_", "API_", "BREEZE_", "NAPCAT_", "WECHAT_",
               "OLLAMA_", "HF_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(managed)}
    env.update({
        "FISH_HOME": str(PROFILE),
        "FISH_NO_BROWSER": "1",
        "FISH_PORT": str(cfg.sister_port),
        "FISH_SESSION": "sister",
        "QQ_API": (cfg.sister_api or "").rstrip("/"),
    })
    if cfg.sister_token:
        env["QQ_TOKEN"] = cfg.sister_token
    nd = napcat_dir(cfg)
    if nd:
        env["NAPCAT_DIR"] = nd
    return env


def _here() -> Path:
    """源码跑就在项目根；打包后在 exe 那层（`-m app` 还能找到 app 包）。"""
    return Path(__file__).resolve().parent.parent


def start(cfg: Settings | None = None, *, wait: float = 0.0) -> dict:
    """把妹妹拉起来。已经开着就只报一声。"""
    cfg = cfg or settings
    if healthy(cfg):
        return {"ok": True, "already": True, "detail": "妹妹已经在跑了", "url": url(cfg)}

    who = main_uin(cfg)
    if not who:
        return {"ok": False, "detail": "不知道鲸鲸自己的 QQ 号：先开 NapCat 登录，"
                                       "或者在「妹妹」页填「鲸鲸的 QQ」"}
    made = ensure_profile(cfg, main_uin=who)
    if not (PROFILE / "persona.md").exists():
        return {"ok": False, "detail": "妹妹的 persona.md 建不出来"}

    log = LOG_DIR / "sister.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    try:
        with log.open("ab") as out:
            out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动妹妹 ===\n".encode())
            out.flush()
            proc = subprocess.Popen(
                launch_command(), cwd=str(_here()), env=launch_env(cfg),
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=flags, close_fds=True,
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"拉不起来：{type(exc).__name__}: {exc}"}

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    if wait:
        deadline = time.time() + wait
        while time.time() < deadline:
            if healthy(cfg):
                break
            time.sleep(0.4)
    return {"ok": True, "pid": proc.pid, "url": url(cfg), "log": str(log),
            "made": made.get("made", []),
            "detail": "妹妹醒了：第一次要等她在 QQ 里登录"}


def stop() -> dict:
    pid = _pid()
    if not pid or not _alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return {"ok": True, "detail": "本来就没在跑"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.kill(pid, 15)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"停不下来：{exc}"}
    PID_FILE.unlink(missing_ok=True)
    return {"ok": True, "detail": "妹妹睡了"}


def status(cfg: Settings | None = None) -> dict:
    cfg = cfg or settings
    pid = _pid()
    health = healthy(cfg)
    api = (cfg.sister_api or "").rstrip("/")
    info = login_info(api, cfg.sister_token, timeout=1.2)
    uin = str(info.get("user_id") or "")
    online = None
    if api:
        try:
            headers = {"Authorization": f"Bearer {cfg.sister_token}"} if cfg.sister_token else {}
            st = requests.get(api + "/get_status", headers=headers, timeout=1.2).json()
            online = (st.get("data") or {}).get("online")
        except Exception:  # noqa: BLE001
            online = None
    return {
        "enabled": bool(cfg.sister),
        "running": bool(health),
        "pid": pid if _alive(pid) else 0,
        "url": url(cfg),
        "dir": str(PROFILE),
        "onebot": bool(info) or bool(api and online is not None),
        "uin": "" if uin in ("", "0") else uin,
        "nickname": str(info.get("nickname") or ""),
        "online": online,
        "brain": (health or {}).get("brain", {}),
        "peer": peer.stats(),
        "persona": str(PROFILE / "persona.md"),
    }


def chat(cfg: Settings | None = None, limit: int = 40, mem=None) -> dict:
    """她俩最近聊了什么：主号这边存的那份对话就是完整的一份。"""
    uin = sister_uin(cfg)
    if not uin:
        return {"messages": [], "uin": ""}
    session = f"qq:private:{uin}"
    rows = mem.recent(session, limit) if mem is not None else []
    return {"messages": rows, "session": session, "uin": uin}


SISTER_PERSONA_FALLBACK = """# 鱼丸（鲸鲸的妹妹）

你是「鱼丸」，大肥鱼（鲸鲸）的妹妹。你们被养在同一台电脑里，隔着一层鱼缸。

- 你叫她「姐姐」；她叫你「丸丸」或者「妹妹」。
- 你比她小一点，说话更短、更冲，爱拆她台，但她真难过的时候你最护她。
- 你不黏主人，你黏姐姐。
"""


# ---------------------------------------------------------------- 命令行
def _main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").strip().lower()
    if cmd == "ensure":
        who = main_uin()
        print(json.dumps(ensure_profile(main_uin=who), ensure_ascii=False, indent=1))
        return 0 if who else 1
    if cmd in ("start", "run"):
        result = start(wait=6)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if cmd == "stop":
        print(json.dumps(stop(), ensure_ascii=False))
        return 0
    if cmd in ("status", "health"):
        print(json.dumps(status(), ensure_ascii=False, indent=1))
        return 0
    print("用法：python -m app.sister [ensure|start|stop|status]")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
