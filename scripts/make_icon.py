# -*- coding: utf-8 -*-
"""从 icon/icon4.png 生成平台图标（macOS .icns + Windows .ico）。

处理步骤：
1. 四角黑色圆角 → 透明（BFS 连通性抠角，只去掉连通黑角，保留蓝色内容）
2. 缩放到目标尺寸生成多尺寸图标
3. macOS 用 iconutil 打包 iconset → .icns；Windows 用 Pillow 多尺寸 → .ico

用法:
    python3 scripts/make_icon.py
输出:
    build_assets/icon.icns
    build_assets/icon.ico
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections import deque

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "icon", "icon4.png")
OUT_DIR = os.path.join(ROOT, "build_assets")


def cut_black_corners(img: Image.Image, dark_thresh: int = 30) -> Image.Image:
    """把四角纯黑色圆角变透明。

    仅从四个角点出发做 BFS 连通，用严格黑阈值（各通道 <30），避免误抠
    主体深蓝渐变/阴影。蓝色内容不连通则保留。
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    def is_dark(p):
        return p[0] < dark_thresh and p[1] < dark_thresh and p[2] < dark_thresh

    queue = deque()
    seen = set()
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if is_dark(px[sx, sy]) and (sx, sy) not in seen:
            seen.add((sx, sy))
            queue.append((sx, sy))
    while queue:
        x, y = queue.popleft()
        px[x, y] = (0, 0, 0, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and is_dark(px[nx, ny]):
                seen.add((nx, ny))
                queue.append((nx, ny))
    return img


def make_icns(src: Image.Image) -> str:
    src = src.resize((1024, 1024), Image.LANCZOS)
    iconset = os.path.join(OUT_DIR, "icon.iconset")
    if os.path.isdir(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset, exist_ok=True)

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
        src.resize((size, size), Image.LANCZOS).save(os.path.join(iconset, name))

    icns = os.path.join(OUT_DIR, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    return icns


def make_ico(src: Image.Image) -> str:
    ico = os.path.join(OUT_DIR, "icon.ico")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    src.save(ico, format="ICO", sizes=sizes)
    return ico


def main():
    if not os.path.isfile(SRC):
        print(f"未找到源图: {SRC}")
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)
    img = Image.open(SRC)
    img = cut_black_corners(img)
    if sys.platform == "darwin" and shutil.which("iconutil"):
        icns = make_icns(img)
        print(f"macOS icns: {icns} ({os.path.getsize(icns)} bytes)")
    ico = make_ico(img)
    print(f"Windows ico: {ico} ({os.path.getsize(ico)} bytes)")


if __name__ == "__main__":
    main()
