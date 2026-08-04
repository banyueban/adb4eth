# -*- coding: utf-8 -*-
"""打包后的资源定位：优先使用包内随附的 adb（platform-tools），否则退回系统 PATH。"""
from __future__ import annotations

import os
import shutil
import sys


def bundle_root() -> str:
    """打包(frozen)时为解包目录，否则为包根目录（开发模式）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def adb_candidates() -> list:
    root = bundle_root()
    return [os.path.join(root, "platform-tools", "adb"), os.path.join(root, "adb")]


def ensure_adb_on_path():
    """若包内带 adb 则注入 PATH 并返回其路径；否则返回系统 adb 路径（可能为 None）。"""
    for cand in adb_candidates():
        exe = cand + (".exe" if sys.platform.startswith("win") else "")
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            d = os.path.dirname(exe)
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return exe
    return shutil.which("adb")
