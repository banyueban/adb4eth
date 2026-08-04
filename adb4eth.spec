# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（跨平台）。

用法:
    python3 -m pip install pyinstaller
    python3 -m PyInstaller adb4eth.spec --noconfirm

产物（在对应平台上构建）:
    macOS:    dist/adb4eth/adb4eth   +  dist/adb4eth.app
    Windows:  dist/adb4eth/adb4eth.exe

说明:
- 应用入口为 adb4eth/gui_main.py（双击直接打开 GUI）。
- 若仓库根目录存在 platform-tools/，整目录随包附带，运行时优先使用。
- onedir 模式：datas 目录放入 EXE 产物目录下；macOS 再包成 .app。
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

# onedir：EXE 带上 binaries 与 datas（统一结构，dist/adb4eth/<可执行文件>）
exe_args = [
    pyz, a.scripts, a.binaries, a.datas, [],
]
exe_kwargs = dict(
    name="adb4eth",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
if is_win:
    exe_kwargs["icon"] = os.path.join(repo_root, "build_assets", "icon.ico")

exe = EXE(*exe_args, **exe_kwargs)

if not is_win:
    app = BUNDLE(
        exe,
        name="adb4eth.app",
        icon=os.path.join(repo_root, "build_assets", "icon.icns"),
        bundle_identifier="com.adb4eth.tool",
    )
