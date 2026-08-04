# -*- coding: utf-8 -*-
"""打包后的应用入口：双击 .app/.exe 时直接打开图形界面。

注意：这是 PyInstaller 的顶层入口脚本，须用绝对导入（无包上下文）。
"""
from adb4eth.gui import main

if __name__ == "__main__":
    main()
