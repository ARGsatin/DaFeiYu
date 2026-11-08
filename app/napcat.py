"""NapCat 的一键开关：面板里点「启动」，不用再去双击那个 bat。

真正的启动器还是 NapCat 自己那份 `startNapCat.ps1`（提权 → 注入 → 拉 QQ → 没登录就把二维码存到
`cache/qrcode.png`）。这里只负责三件事：

1. 找到 NapCat 装在哪（`NAPCAT_DIR`，或 D:\\NapCat / C:\\NapCat 这些常见位置）；
2. 以管理员身份把它拉起来——`Start-Process -Verb RunAs`，会弹 UAC，用户点「是」；
3. 回报「QQ 起没起 / 3000 通没通 / 登没登录 / 二维码新不新」，面板拿去做进度条和扫码图。

只读那一个二维码文件，不改 NapCat 的任何配置。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import ROOT, settings

# 常见安装位置，按顺序找
CANDIDATES = (
    Path("D:/NapCat"),
    Path("C:/NapCat"),
    Path("D:/napcat"),
    ROOT / "NapCat",
    Path.home() / "NapCat",
    Path.home() / "Desktop" / "NapCat",
    Path.home() / "Desktop" / "napcat",
)

# 刚点过启动就别再弹一次 UAC（两份 NapCat 各记各的）
START_COOLDOWN = 25
_last_start: dict[str, float] = {"main": 0.0, "sister": 0.0}
_last_error: dict[str, str] = {"main": "", "sister": ""}


# ---------------------------------------------------------------- 找目录
def _looks_like(p: Path) -> bool:
    try:
        return p.is_dir() and (
            (p / "startNapCat.ps1").is_file() or (p / "NapCatWinBootMain.exe").is_file()
        )
    except OSError:
        return False


def napcat_dir() -> Path | None:
    raw = (getattr(settings, "napcat_dir", "") or "").strip().strip('"')
    if raw:
        p = Path(raw).expanduser()
        return p if _looks_like(p) else None
    for p in CANDIDATES:
        if _looks_like(p):
            return p
    return None


# ---------------------------------------------------------------- 看状态
def _target(sister: bool = False) -> tuple[Path | None, str, str]:
    """这次看的是哪一份：鲸鲸那份（3000）还是鱼丸那份（3001）。

    妹妹那份的目录来自 sister.napcat_dir()（设置里的「妹妹的 NapCat 目录」，
    留空就在鲸鲸那份旁边找 NapCat2）——绝不能回落到鲸鲸那份：
    那份目录里的启动脚本会先关掉本机所有 QQ。
    """
    if sister:
        try:
            from . import sister as sister_mod
            raw = sister_mod.napcat_dir()
        except Exception:  # noqa: BLE001
            raw = ""
        d = Path(raw).expanduser() if raw else None
        return (d if d and _looks_like(d) else None,
                (settings.sister_api or "").rstrip("/"), settings.sister_token)
    return napcat_dir(), (settings.qq_api or "").rstrip("/"), settings.qq_token


def _headers(sister: bool = False) -> dict:
    token = settings.sister_token if sister else settings.qq_token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _onebot(timeout: float = 1.5, sister: bool = False) -> dict | None:
    _dir, base, _token = _target(sister)
    if not base:
        return None
    try:
        r = requests.get(base + "/get_status", headers=_headers(sister), timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _login_info(timeout: float = 1.5, sister: bool = False) -> dict:
    _dir, base, _token = _target(sister)
    if not base:
        return {}
    try:
        return (requests.get(base + "/get_login_info", headers=_headers(sister),
                             timeout=timeout).json().get("data") or {})
    except Exception:  # noqa: BLE001
        return {}


def _qq_running() -> bool:
    """NapCat 是注进 QQ 进程里的，QQ 在就说明「注入过、进程还在」。

    用 Toolhelp 快照查，不调 tasklist：以前每 2.5 秒起一个 tasklist，
    打包成窗口程序之后每次都闪一下黑框。
    """
    return _qq_count() > 0


def _qq_count() -> int:
    """本机有几个 QQ.exe。两份 NapCat 时这个数会明显变大（一份就有好几个进程）。"""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        import ctypes.wintypes as wt

        TH32CS_SNAPPROCESS = 0x2

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (0, -1):
            return 0
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        n = 0
        try:
            more = kernel32.Process32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.szExeFile.decode("mbcs", "ignore").lower() == "qq.exe":
                    n += 1
                more = kernel32.Process32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return n
    except Exception:  # noqa: BLE001
        return 0


def _webui_url(d: Path | None) -> str:
    if not d:
        return ""
    try:
        cfg = json.loads((d / "config" / "webui.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    host = str(cfg.get("host") or "127.0.0.1")
    if host in ("::", "0.0.0.0", ""):
        host = "127.0.0.1"
    url = f"http://{host}:{cfg.get('port') or 6099}/webui"
    token = str(cfg.get("token") or "")
    return f"{url}?token={token}" if token else url


def _log_tail(d: Path | None, n: int = 6) -> list[str]:
    if not d:
        return []
    try:
        lines = (d / "logs" / "startup.log").read_text(
            encoding="utf-8", errors="replace").splitlines()
        return [ln.strip() for ln in lines[-n:] if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


def _qrcode_path(d: Path | None) -> Path | None:
    if not d:
        return None
    p = d / "cache" / "qrcode.png"
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def _webui_alive(d: Path | None, timeout: float = 0.5) -> bool:
    """NapCat 自己的网页面板在不在听。

    只探端口，不发请求：面板 2.5 秒问一次，探一个本地端口几乎不花时间；
    探不到就说明 NapCat 内核还没起来（QQ 还停在登录界面时就是这样），
    这时候别把用户送到一个打不开的页面上。
    """
    url = _webui_url(d)
    if not url:
        return False
    try:
        parts = urlparse(url)
        host = parts.hostname or "127.0.0.1"
        port = int(parts.port or 6099)
    except (ValueError, AttributeError):
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def status(timeout: float = 1.5, sister: bool = False) -> dict:
    key = "sister" if sister else "main"
    d, api, _token = _target(sister)
    now = time.time()
    last = _last_start.get(key, 0.0)
    qr = _qrcode_path(d)
    qr_mtime = qr.stat().st_mtime if qr else 0.0
    # 姐姐那份看「本机有没有 QQ」；妹妹那份看它自己的 6100 面板在不在听
    # （两份都在跑时，QQ 进程数分不出是谁的）
    running = _webui_alive(d) if sister else _qq_running()
    st: dict = {
        "target": key,
        "found": bool(d),
        "dir": str(d) if d else "",
        "api": api,
        "running": running,
        "onebot": False,
        "online": False,
        "uin": "",
        "nickname": "",
        "webui": _webui_url(d),
        "webui_up": _webui_alive(d),
        "starting": (now - last) < 120 if last else False,
        "last_start": int(last),
        "last_error": _last_error.get(key, ""),
        "qq_count": _qq_count(),
        "qrcode": {
            "has": bool(qr),
            "mtime": int(qr_mtime),
            "age": int(now - qr_mtime) if qr else None,
        },
        "log": _log_tail(d),
    }

    data = _onebot(timeout, sister=sister)
    if data:
        st["onebot"] = True
        info = _login_info(timeout, sister=sister)
        uin = str(info.get("user_id") or "")
        st["uin"] = "" if uin == "0" else uin
        st["nickname"] = str(info.get("nickname") or "")
        st["online"] = bool((data.get("data") or {}).get("online")) and bool(st["uin"])

    # 二维码只在这几种时候值得看：还没登录、而且图是最近画的
    qr_fresh = bool(qr) and (now - qr_mtime) < 600 and (
        st["onebot"] or st["starting"] or st["running"]
    )
    st["need_scan"] = bool(st["onebot"] and not st["online"])
    st["show_qr"] = bool(qr_fresh and (st["need_scan"] or st["starting"]))
    return st


# ---------------------------------------------------------------- 拉起来
def launch_command(d: Path, whatif: bool = False) -> list[str]:
    """拼出「以管理员身份跑 startNapCat.ps1」那条命令；拆出来方便测。"""
    script = d / "startNapCat.ps1"
    # -ArgumentList 用一个整串，路径里有空格也不会被拆开
    args = f'-NoProfile -ExecutionPolicy Bypass -File "{script}"'
    inner = (
        "Start-Process -FilePath 'powershell.exe' -Verb RunAs "
        f"-ArgumentList '{args}' "
        f"-WorkingDirectory '{d}'"
    )
    if whatif:
        inner += " -WhatIf"
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", inner]


def start(sister: bool = False) -> dict:
    key = "sister" if sister else "main"
    d, _api, _token = _target(sister)
    if not d:
        if sister:
            return {"ok": False, "detail": "没找到鱼丸那份 NapCat：设置 → 🐠 妹妹 →「妹妹的 NapCat 目录」"
                                           "填 D:\\NapCat2（别填鲸鲸那份，那个会关掉所有 QQ）"}
        return {"ok": False, "detail": "没找到 NapCat 目录：到「高级选项 → NapCat 目录」里填一下（比如 D:\\NapCat）"}

    last = _last_start.get(key, 0.0)
    waited = time.time() - last
    if last and waited < START_COOLDOWN:
        return {"ok": False, "detail": f"刚点过（{int(waited)} 秒前），先看看 UAC 窗口；还不行就再点"}

    if not (d / "startNapCat.ps1").is_file():
        return {"ok": False, "detail": f"{d} 里没有 startNapCat.ps1，这套 NapCat 装得不完整"}

    # Start-Process -Verb RunAs 会弹 UAC；管理员窗口里跑的就是你平时双击的那套
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(launch_command(d), cwd=str(d), creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        _last_error[key] = f"{type(exc).__name__}: {exc}"
        return {"ok": False, "detail": f"拉不起来：{_last_error[key]}"}

    _last_start[key] = time.time()
    _last_error[key] = ""
    if sister:
        return {
            "ok": True,
            "detail": "正在申请管理员权限：UAC 点「是」。会弹出第二个 QQ 窗口——"
                      "用手机登鱼丸那个号，登进去 3001 才通（这份脚本不会关掉鲸鲸的 QQ）",
        }
    return {
        "ok": True,
        "detail": "正在申请管理员权限：UAC 弹窗点「是」，然后等它把 QQ 拉起来（要几十秒）",
    }


def qrcode(sister: bool = False) -> tuple[bytes, float] | None:
    """二维码图片 + 修改时间；面板拿 mtime 当缓存键。"""
    d, _api, _token = _target(sister)
    p = _qrcode_path(d)
    if not p:
        return None
    try:
        data = p.read_bytes()
        if len(data) < 100:
            return None
        return data, p.stat().st_mtime
    except OSError:
        return None
