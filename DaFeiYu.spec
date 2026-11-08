# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：大肥鱼桌面版（onedir，带窗口图标）。

打包：
    python -m PyInstaller --noconfirm --clean DaFeiYu.spec

产物在 dist/DaFeiYu/，双击 DaFeiYu.exe 就行。
"""
from PyInstaller.utils.hooks import collect_submodules

# uvicorn 的 event loop / 协议实现都是运行时按名字导入的，得全部收进来
hiddenimports = collect_submodules("uvicorn")
hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium"]

# 只读资源：网页、默认人格、表情包、图标、.env 模板
datas = [
    ("web", "web"),
    ("personas", "personas"),
    ("stickers", "stickers"),
    ("assets", "assets"),
    ("persona.md", "."),
    ("personas.json", "."),
    (".env.example", "."),
    # 妹妹的人格模板：第一次启动妹妹时复制到 %APPDATA%\大肥鱼\sister\
    ("sister/persona.md", "sister"),
    ("sister/personas.json", "sister"),
]

# 这些是可选的大块头（本地模型 / 语音识别），桌面版先不背
excludes = [
    "faster_whisper", "ctranslate2", "av", "onnxruntime",
    "torch", "transformers", "accelerate", "sentencepiece",
    "tkinter", "matplotlib", "pandas", "scipy", "IPython", "pytest",
]

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DaFeiYu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/fish.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DaFeiYu",
)
