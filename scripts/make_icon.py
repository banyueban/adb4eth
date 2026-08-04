# -*- coding: utf-8 -*-
"""从 icon/icon.png 生成平台图标：
- macOS: .icns（用 iconutil 打包 iconset）
- Windows: .ico（多尺寸）

用法:
    python3 scripts/make_icon.py
输出:
    build_assets/icon.icns
    build_assets/icon.ico
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "icon", "icon.png")
OUT_DIR = os.path.join(ROOT, "build_assets")


def make_icns() -> str:
    src = Image.open(SRC).convert("RGBA")
    # icns 最大 1024，先缩到 1024 保证 alpha 质量
    src = src.resize((1024, 1024), Image.LANCZOS)
    iconset = os.path.join(OUT_DIR, "icon.iconset")
    if os.path.isdir(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset, exist_ok=True)

    # iconutil 需要的各尺寸
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, size in sizes.items():
        img = src.resize((size, size), Image.LANCZOS)
        img.save(os.path.join(iconset, name))

    icns = os.path.join(OUT_DIR, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    return icns


def make_ico() -> str:
    src = Image.open(SRC).convert("RGBA")
    ico = os.path.join(OUT_DIR, "icon.ico")
    # ico 常见尺寸
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    src.save(ico, format="ICO", sizes=sizes)
    return ico


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if sys.platform == "darwin" and shutil.which("iconutil"):
        icns = make_icns()
        print(f"macOS icns: {icns} ({os.path.getsize(icns)} bytes)")
    ico = make_ico()
    print(f"Windows ico: {ico} ({os.path.getsize(ico)} bytes)")


if __name__ == "__main__":
    main()
