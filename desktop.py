"""大肥鱼的桌面壳（轻量版）：后台跑服务 + 托盘 + 无地址栏窗口。

双击「启动大肥鱼.vbs」就是它。服务还是原来那个 FastAPI，页面还是 `web/index.html`，
这里只负责四件小事：

1. 只开一份（命名 mutex，重复双击只会把窗口叫出来）；
2. 把 `python -m app` 藏在后台跑，日志写进 `data/app.log`（不再有黑窗口）；
3. 用 Edge 的 `--app` 开一个没有地址栏的窗口，看起来就是个 App；
4. 托盘里能打开 / 重启 / 退出，还能一键开关「开机自启」。

以后打包成 exe（桌面版 A 阶段）时，这个文件就是入口。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR, settings  # noqa: E402

APP_NAME = "大肥鱼"
HOST = settings.host if settings.host not in ("", "0.0.0.0", "::") else "127.0.0.1"
URL = f"http://{HOST}:{settings.port}"
ICON_PNG = ROOT / "assets" / "fish.png"
ICON_ICO = ROOT / "assets" / "fish.ico"
# Edge 的独立配置目录：别塞进 OneDrive，浏览器的缓存文件太多了
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA") or DATA_DIR) / "大肥鱼" / "edge-app"

_mutex = None
_PORT_FILE = "desktop.port"
_LOG_FILE = "desktop.log"
_log_lock = threading.Lock()


def _log(message: str) -> None:
    """把桌面壳自己的动静写进 data/desktop.log，出问题时能查（pythonw 没有控制台）。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n"
        with _log_lock:
            with open(DATA_DIR / _LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def _log_exc(prefix: str) -> None:
    try:
        _log(prefix + "\n" + traceback.format_exc())
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 把已有窗口叫出来
def _start_raise_server(window) -> None:
    """第二份双击时，让已经在跑的那一份把窗口 show 出来（本地端口，只认 show 一句话）。

    window 为 None 时（Edge 窗口模式没有可 show 的原生窗口），收到 show 就再开一个窗口。
    """
    import socket

    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        (DATA_DIR / _PORT_FILE).write_text(str(port), encoding="utf-8")
    except OSError:
        _log_exc("raise-server: bind failed")
        return

    def loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.settimeout(1.0)
                data = conn.recv(64)
                if b"show" in data:
                    if window is not None:
                        _show_window(window)
                    else:
                        open_window()
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()


def raise_running() -> bool:
    """告诉已经在跑的那一份：把窗口亮出来。没人在听就返回 False。"""
    import socket

    try:
        port = int((DATA_DIR / _PORT_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
            sock.sendall(b"show\n")
        return True
    except OSError:
        return False


def _clear_port_file() -> None:
    try:
        (DATA_DIR / _PORT_FILE).unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------- 基本检查
def claim_single_instance() -> bool:
    """真·单实例：已经有托盘在跑就返回 False。"""
    global _mutex
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _mutex = kernel32.CreateMutexW(None, False, "Local\\DaFeiYuDesktop")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


def service_up(timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def find_edge() -> Path | None:
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                ) as key:
                    p = Path(str(winreg.QueryValue(key, None)))
                    if p.is_file():
                        return p
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    for p in (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------- 服务
class Service:
    """把 `python -m app` 当成子进程管：起、停、看它活没活。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.started_by_us = False
        self._log = None

    def running(self) -> bool:
        return service_up()

    def start(self, wait: float = 45.0) -> bool:
        if self.running():
            _log("service: already running, attach")
            return True
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_path = DATA_DIR / "app.log"
        # 日志只留最近这一份，超过 2MB 就把旧的挪成 app.log.1（uvicorn 的访问日志涨得挺快）
        try:
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                log_path.replace(DATA_DIR / "app.log.1")
        except OSError:
            pass
        self._log = open(log_path, "ab", buffering=0)
        env = dict(os.environ, FISH_NO_BROWSER="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # 打包成 exe 之后没法 `-m app`，改成让自己带 --serve 再跑一份
        cmd = [sys.executable, "--serve", "--parent", str(os.getpid())] \
            if getattr(sys, "frozen", False) else [sys.executable, "-m", "app"]
        _log("service: starting " + " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT), env=env,
            stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        _log(f"service: spawned pid={self.proc.pid}")
        self.started_by_us = True
        end = time.time() + wait
        while time.time() < end:
            if self.running():
                _log("service: up and healthy")
                return True
            if self.proc.poll() is not None:
                _log(f"service: exited early rc={self.proc.returncode}")
                return False
            time.sleep(0.4)
        _log("service: start timed out")
        return False

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        self.started_by_us = False
        if proc and proc.poll() is None:
            _log(f"service: stopping pid={proc.pid}")
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if self._log:
            try:
                self._log.close()
            except Exception:  # noqa: BLE001
                pass
            self._log = None


def start_watchdog(svc: "Service", interval: float = 10.0) -> threading.Thread:
    """托盘还在、后台服务却没了的话，自动把她拉起来（不再双击没反应）。"""

    def loop() -> None:
        misses = 0
        while True:
            time.sleep(interval)
            try:
                if svc.running():
                    misses = 0
                    continue
                misses += 1
                if misses < 2:
                    continue
                _log("watchdog: service is down, restarting")
                svc.stop()
                ok = svc.start()
                _log("watchdog: restart " + ("ok" if ok else "failed"))
                misses = 0
            except Exception:  # noqa: BLE001
                _log_exc("watchdog: unexpected error")
                misses = 0

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------- 窗口
def native_available() -> bool:
    try:
        import webview  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _call_first(obj, names: tuple[str, ...]) -> bool:
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                fn()
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _hide_window(win) -> bool:
    return _call_first(win, ("hide", "minimize"))


def _show_window(win) -> bool:
    return _call_first(win, ("show", "restore"))


def open_window(*_args) -> None:
    """源码模式：用 Edge 的 --app 开一个没有地址栏的窗口。"""
    edge = find_edge()
    if edge:
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [
                    str(edge),
                    f"--app={URL}",
                    "--window-size=1180,900",
                    f"--user-data-dir={PROFILE_DIR}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            _log(f"window: edge launched pid={proc.pid}")
            return
        except Exception:  # noqa: BLE001
            _log_exc("window: edge launch failed, fallback to browser")
    else:
        _log("window: edge not found, fallback to browser")
    try:
        webbrowser.open(URL)
        _log("window: default browser requested")
    except Exception:  # noqa: BLE001
        _log_exc("window: default browser failed")


# ---------------------------------------------------------------- 开机自启
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DaFeiYu"


def autostart_target() -> str:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):        # 以后打包成 exe 就是这个
        return f'"{exe}" --quiet'
    pyw = exe.with_name("pythonw.exe")
    if not pyw.is_file():
        pyw = exe
    return f'"{pyw}" "{ROOT / "desktop.py"}" --quiet'


def autostart_on() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, RUN_NAME)[0])
    except Exception:  # noqa: BLE001
        return False


def set_autostart(on: bool) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_target())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return autostart_on() == on
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 托盘
def build_tray(svc: Service, window=None):
    import pystray
    from PIL import Image

    image = Image.open(ICON_PNG).convert("RGBA")

    def do_open(icon=None, item=None):
        if window is not None and _show_window(window):
            return
        open_window()

    def do_restart(icon, item):
        if not svc.started_by_us:
            icon.notify("这个服务是从外面启动的，先关掉原来那个再来", APP_NAME)
            return
        icon.notify("重启中…", APP_NAME)
        svc.stop()
        ok = svc.start()
        if ok and window is not None:
            try:
                window.load_url(URL)
            except Exception:  # noqa: BLE001
                pass
        icon.notify("好了" if ok else "没起来，点「看日志」看看", APP_NAME)

    def do_toggle_autostart(icon, item):
        on = not autostart_on()
        ok = set_autostart(on)
        icon.notify(
            ("好，下次开机她自己会起来" if on else "好，开机不自启了")
            if ok else "改注册表失败了，用管理员身份再试试",
            APP_NAME,
        )

    def do_data(icon=None, item=None):
        os.startfile(DATA_DIR)

    def do_log(icon=None, item=None):
        log = DATA_DIR / "app.log"
        if log.exists():
            os.startfile(log)
        elif icon:
            icon.notify("还没有日志，她可能还没跑过", APP_NAME)

    def do_quit(icon, item):
        svc.stop()
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开大肥鱼", do_open, default=True),
        pystray.MenuItem("重启服务", do_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("开机自启", do_toggle_autostart, checked=lambda item: autostart_on()),
        pystray.MenuItem("打开数据文件夹", do_data),
        pystray.MenuItem("看日志", do_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", do_quit),
    )
    return pystray.Icon("dafeiyu", image, f"{APP_NAME} · 在缸里", menu)


# ---------------------------------------------------------------- 原生窗口
def work_area() -> tuple[int, int]:
    """屏幕可用区域（去掉任务栏）。窗口别一上来就比屏幕还大。"""
    try:
        import ctypes.wintypes

        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 400 and h > 300:
            return w, h
    except Exception:  # noqa: BLE001
        pass
    return 1280, 800


def run_native(svc: Service, quiet: bool = False, debug: bool = False) -> int:
    """pywebview 的 EdgeChromium 窗口：自己的窗口、自己的图标，关掉收到托盘。"""
    import webview

    wa_w, wa_h = work_area()
    width = max(900, min(1180, wa_w - 60))
    height = max(600, min(900, wa_h - 60))
    window = webview.create_window(
        APP_NAME, URL,
        width=width, height=height, min_size=(900, 600),
        background_color="#04121a",
        hidden=quiet,
    )

    def on_closing(*_args):
        # Event 的约定：返回 False 表示「取消关闭」。先取消，再从别的线程收起来，
        # 免得在 Windows 的 FormClosing 里再调 ShowWindow 自己卡自己。
        threading.Timer(0.05, lambda: _hide_window(window)).start()
        return False

    try:
        window.events.closing += on_closing
    except Exception:  # noqa: BLE001
        pass

    icon = build_tray(svc, window)
    threading.Thread(target=icon.run, daemon=True).start()
    _start_raise_server(window)
    try:
        webview.start(debug=debug)
    finally:
        _clear_port_file()
        svc.stop()
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
    return 0


def serve(parent_pid: int = 0) -> int:
    """`--serve`：被自己拉起来的那个进程，只跑服务、不开窗口。

    主窗口那边要是被强杀了，服务别变成孤儿——盯着父进程的句柄，它一没就自己退。
    """
    # 打包成窗口程序后 stdout 的编码跟着系统走，日志里中文会变乱码，这里固定成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    import uvicorn

    if parent_pid:
        threading.Thread(target=_exit_with_parent, args=(parent_pid,), daemon=True).start()

    from app.server import app as fastapi_app

    uvicorn.run(fastapi_app, host=settings.host, port=settings.port, log_level="info")
    return 0


def _exit_with_parent(parent_pid: int) -> None:
    try:
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if not handle:
            return
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(0)
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------- 入口
def _check() -> int:
    info = {
        "url": URL,
        "python": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "edge": str(find_edge() or ""),
        "service_up": service_up(),
        "autostart": autostart_on(),
        "autostart_cmd": autostart_target(),
        "data_dir": str(DATA_DIR),
        "icon": str(ICON_PNG if ICON_PNG.exists() else ""),
    }
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    # 被自己拉起来的服务进程：只跑 uvicorn，别去抢托盘
    if "--serve" in sys.argv[1:]:
        parent = 0
        if "--parent" in sys.argv:
            try:
                parent = int(sys.argv[sys.argv.index("--parent") + 1])
            except (IndexError, ValueError):
                parent = 0
        return serve(parent)

    parser = argparse.ArgumentParser(description="大肥鱼桌面版")
    parser.add_argument("--quiet", action="store_true", help="不自动开窗口（开机自启用）")
    parser.add_argument("--native", action="store_true",
                        help="用 pywebview 的原生窗口（装了 pywebview 时默认就是）")
    parser.add_argument("--edge", action="store_true",
                        help="强制用 Edge 的无地址栏窗口（不开 pywebview）")
    parser.add_argument("--debug", action="store_true", help="开 devtools")
    parser.add_argument("--check", action="store_true", help="只打印环境，不启动")
    parser.add_argument("--start-only", type=int, default=0, metavar="SECONDS",
                        help="起服务、等 N 秒、再关掉（自检用）")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except Exception:  # noqa: BLE001
        _log_exc("unhandled exception in desktop shell")
        if not args.quiet:
            try:
                ctypes.windll.user32.MessageBoxW(
                    0, "大肥鱼桌面壳出错了。\n\n详情看 data\\desktop.log。", APP_NAME, 0x10
                )
            except Exception:  # noqa: BLE001
                pass
        return 1


def _run(args) -> int:
    if args.check:
        return _check()

    native = args.native or (native_available() and not args.edge)

    if not claim_single_instance():
        # 已经有一只了：能把她叫出来就叫，叫不出来再开一个窗口
        _log("single-instance: already running, trying to raise")
        if not raise_running():
            _log("single-instance: raise failed, opening a new window")
            open_window()
        return 0

    _log(f"starting desktop shell pid={os.getpid()} native={native}")
    svc = Service()
    ok = svc.start()
    if not ok:
        msg = "服务没起来。\n\n看看 data\\app.log 里写了什么。"
        _log("startup failed: service did not come up")
        if args.start_only:
            print(msg.replace("\n\n", " "))
            return 1
        if not args.quiet:
            try:
                ctypes.windll.user32.MessageBoxW(0, msg, APP_NAME, 0x10)
            except Exception:  # noqa: BLE001
                pass
        return 1

    if args.start_only:
        print(f"服务起来了：{URL}")
        time.sleep(args.start_only)
        svc.stop()
        print("已关掉")
        return 0

    if svc.started_by_us:
        start_watchdog(svc)

    if native:
        try:
            return run_native(svc, quiet=args.quiet, debug=args.debug)
        except Exception:  # noqa: BLE001
            _log_exc("native window failed, falling back to Edge window")

    _start_raise_server(None)
    if not args.quiet:
        threading.Timer(1.0, open_window).start()

    icon = build_tray(svc)
    try:
        icon.run()
    finally:
        _clear_port_file()
        svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
