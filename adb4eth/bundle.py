# -*- coding: utf-8 -*-
"""打包后的资源定位：优先使用包内随附的 adb（platform-tools），否则退回系统 PATH。"""
from __future__ import annotations

import os
import shutil
import sys

if sys.platform.startswith("win"):
    _ADB_EXE = "adb.exe"
else:
    _ADB_EXE = "adb"


def bundle_roots() -> list:
    """候选资源根目录。打包(frozen)时包含 _MEIPASS 解包目录与可执行文件目录；
    开发模式返回仓库根目录。"""
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(sys.executable))
    else:
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return roots


def adb_candidates() -> list:
    cands = []
    for root in bundle_roots():
        cands.append(os.path.join(root, "platform-tools", _ADB_EXE))
        cands.append(os.path.join(root, _ADB_EXE))
    return cands


def ensure_adb_on_path():
    """若包内带 adb 则注入 PATH 并返回其路径；否则返回系统 adb 路径（可能为 None）。"""
    for cand in adb_candidates():
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            d = os.path.dirname(cand)
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return cand
    return shutil.which(_ADB_EXE)
