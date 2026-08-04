# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（macOS 上打 macOS 包）:
    python3 -m pip install pyinstaller
    python3 -m PyInstaller adb4eth.spec

产物:
    dist/adb4eth.app        （macOS 可双击应用）
    dist/adb4eth            （macOS 命令行可执行文件）

注意:
- adb4eth.spec 会尝试把 <repo>/platform-tools/adb 打进包内（随附 adb），
  若该目录不存在则自动跳过，运行时回退到系统 PATH 中的 adb。
- 每个平台需在对应平台上各自打包（macOS 打不出 Windows exe）。
- macOS 首次打开.app 若被 Gatekeeper 拦截：右键 → 打开 → 仍要打开。
"""
import os

repo_root = os.path.dirname(os.path.abspath(SPEC))

datas = []

# 若仓库内已有 platform-tools/adb，则打进包内（GUI 启动时会注入 PATH）
adb_dir = os.path.join(repo_root, "platform-tools")
if os.path.isdir(adb_dir):
    datas.append((adb_dir, "platform-tools"))

a = Analysis(
    ["adb4eth/__main__.py"],
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
    console=True,
)

app = BUNDLE(
    exe,
    name="adb4eth.app",
    icon=None,
    bundle_identifier="com.adb4eth.tool",
)
