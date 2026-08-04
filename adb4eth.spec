# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（跨平台）。

用法:
    python3 -m pip install pyinstaller
    python3 -m PyInstaller adb4eth.spec --noconfirm

产物（在对应平台上构建）:
    macOS:    dist/adb4eth.app   （双击打开图形界面）
    Windows:  dist/adb4eth.exe   （图形界面，不弹控制台）

说明:
- 应用入口为 adb4eth/gui_main.py（双击直接打开 GUI）。
- 若仓库根目录存在 platform-tools/adb，会随包附带，运行时优先使用。
- Windows 用 windowed（--noconsole）；macOS 用 BUNDLE 生成 .app。
"""
import os
import sys

repo_root = os.path.dirname(os.path.abspath(SPEC))
is_win = sys.platform.startswith("win")

datas = []
adb_dir = os.path.join(repo_root, "platform-tools")
if os.path.isdir(adb_dir):
    datas.append((adb_dir, "platform-tools"))

a = Analysis(
    ["adb4eth/gui_main.py"],
    pathex=[repo_root],
    binaries=[],
    datas=datas,
    hiddenimports=["customtkinter", "tkinter"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if is_win:
    # Windows: 图形界面不弹控制台
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="adb4eth",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
else:
    # macOS: 先生成可执行文件，再用 BUNDLE 包成 .app
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="adb4eth",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    app = BUNDLE(
        exe,
        name="adb4eth.app",
        icon=None,
        bundle_identifier="com.adb4eth.tool",
    )
