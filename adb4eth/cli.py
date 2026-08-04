#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adb4eth CLI 入口。

用法:
    python -m adb4eth                 # 完整检测+配置
    python -m adb4eth --no-config     # 只检测不配置
    python -m adb4eth --report out.md # 输出报告文件
    python -m adb4eth --net 192.168.100  --pc-ip 192.168.100.1 --reg-ip 192.168.100.2
"""
from __future__ import annotations

import argparse
import sys

from .models import RunContext, mask_to_prefix
from .orchestrator import Orchestrator
from .reporter import Reporter


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="adb4eth", description="有线 ADB 调试自动检测与配置")
    p.add_argument("--platform", choices=["macos", "windows"], default=None, help="覆盖平台检测")
    p.add_argument("--no-config", action="store_true", help="只检测，不修改任何配置")
    p.add_argument("--report", metavar="PATH", help="Markdown 报告输出路径")
    p.add_argument("--net", default="192.168.100", help="调试网段前缀(默认 192.168.100)")
    p.add_argument("--pc-ip", default=None, help="PC 端 IP(默认 <net>.1)")
    p.add_argument("--reg-ip", default=None, help="收银机 IP(默认 <net>.2)")
    p.add_argument("--adb-port", type=int, default=5555)
    args = p.parse_args(argv)

    net = args.net
    pc_ip = args.pc_ip or f"{net}.1"
    reg_ip = args.reg_ip or f"{net}.2"

    ctx = RunContext(
        platform=args.platform or ("windows" if sys.platform.startswith("win") else "macos"),
        debug_net=net,
        pc_ip=pc_ip,
        reg_ip=reg_ip,
        adb_port=args.adb_port,
    )

    if args.no_config:
        Orchestrator(ctx).run_detect_only()
    else:
        Orchestrator(ctx).run()

    reporter = Reporter(ctx)
    print(reporter.console())
    if args.report:
        path = reporter.save(args.report)
        print(f"\n报告已保存: {path}")

    # 退出码：有 FAIL 返回 1
    fails = [r for r in ctx.results if r.status == "FAIL"]
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
