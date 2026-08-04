#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys


def main():
    if "--gui" in sys.argv:
        from .gui import main as gui_main
        args = [a for a in sys.argv[1:] if a != "--gui"]
        platform = None
        for i, a in enumerate(args):
            if a == "--platform" and i + 1 < len(args):
                platform = args[i + 1]
        gui_main(platform=platform)
        return 0
    from .cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
