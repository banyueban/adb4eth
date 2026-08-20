"""adb4eth CLI 入口。

用法:
    python -m adb4eth                 # 完整检测+配置（多网卡时交互选择）
    python -m adb4eth --no-config     # 只检测不配置
    python -m adb4eth --iface en11    # 指定网卡，跳过交互选择
    python -m adb4eth --list-ifaces   # 仅列出候选网卡后退出
    python -m adb4eth --report out.md # 输出报告文件
    python -m adb4eth --net 192.168.100 --pc-ip 192.168.100.1 --reg-ip 192.168.100.2
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from .detectors.topology import auto_select, build_candidates
from .models import NetIface, RunContext
from .orchestrator import Orchestrator
from .reporter import Reporter


def describe_iface(c: NetIface, def_if: str | None) -> str:
    parts = [c.name, c.vendor or c.iftype]
    parts.append("up" if c.link_up else "down")
    if c.ip:
        parts.append(c.ip)
    if c.media:
        parts.append(c.media)
    if c.is_usb:
        parts.append("USB")
    if def_if and c.name == def_if:
        parts.append("默认路由")
    return "  ".join(parts)


def resolve_iface_arg(candidates: list[NetIface], name: str) -> NetIface | None:
    for c in candidates:
        if c.name == name:
            return c
    return None


def interactive_select(
    candidates: list[NetIface],
    ctx: RunContext,
    full_mode: bool,
    stdin=None,
    stdout=None,
) -> NetIface | None:
    """交互选择：返回用户选中的网卡；取消/回车返回自动选择结果；放弃返回 None。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if len(candidates) == 1:
        return candidates[0]
    stdout.write(f"\n发现 {len(candidates)} 个有线网卡候选：\n")
    for idx, c in enumerate(candidates, 1):
        warn = (
            "  ⚠ 默认路由"
            if ctx.default_route_iface and c.name == ctx.default_route_iface
            else ""
        )
        stdout.write(f"  [{idx}] {describe_iface(c, ctx.default_route_iface)}{warn}\n")
    while True:
        try:
            stdout.write("请输入序号（回车=自动选择）: ")
            stdout.flush()
            line = (stdin.readline() or "").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not line:
            return auto_select(candidates, ctx.default_route_iface)
        try:
            idx = int(line)
        except ValueError:
            stdout.write("输入无效，请输入序号。\n")
            continue
        if 1 <= idx <= len(candidates):
            chosen = candidates[idx - 1]
            if (
                full_mode
                and ctx.default_route_iface
                and chosen.name == ctx.default_route_iface
            ):
                stdout.write(
                    "该网卡是当前默认路由网卡，配置可能断开现有网络，仍要继续吗？(y/N) "
                )
                stdout.flush()
                try:
                    ans = (stdin.readline() or "").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                if ans not in ("y", "yes"):
                    stdout.write("已取消选择。\n")
                    return None
            return chosen
        stdout.write("序号超出范围，请重新输入。\n")


def make_selector(
    ctx: RunContext, full_mode: bool, iface_arg: str | None = None
) -> Callable[[list[NetIface]], NetIface | None] | None:
    """构造 CLI 网卡选择回调；非交互且未指定 --iface 时返回 None（自动选择）。"""
    if iface_arg:

        def select(candidates: list[NetIface]) -> NetIface | None:
            chosen = resolve_iface_arg(candidates, iface_arg)
            if chosen is None:
                print(f"错误：未找到网卡 '{iface_arg}'，可用候选：", file=sys.stderr)
                for c in candidates:
                    print(f"  - {c.name}", file=sys.stderr)
                return None
            if (
                full_mode
                and ctx.default_route_iface
                and chosen.name == ctx.default_route_iface
            ):
                print(
                    f"警告：{chosen.name} 是默认路由网卡，配置可能断开现有网络。",
                    file=sys.stderr,
                )
            return chosen

        return select
    if sys.stdin.isatty():
        return lambda cands: interactive_select(cands, ctx, full_mode)
    print(
        "非交互环境（stdin 非 tty），自动选择调试网卡；可用 --iface 指定。",
        file=sys.stderr,
    )
    return None


def list_ifaces(platform: str | None = None) -> int:
    from .platform.base import create_adapter

    adapter = create_adapter(platform)
    try:
        ifaces = adapter.list_interfaces()
        def_if = adapter.get_default_route_iface()
    except Exception as e:
        print(f"枚举网卡失败：{e}", file=sys.stderr)
        return 1
    candidates = build_candidates(ifaces, def_if)
    if not candidates:
        print("未发现有线网卡候选。")
        return 1
    print(f"发现 {len(candidates)} 个有线网卡候选：")
    for c in candidates:
        warn = "  ⚠ 默认路由" if def_if and c.name == def_if else ""
        print(f"  - {describe_iface(c, def_if)}{warn}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="adb4eth", description="有线 ADB 调试自动检测与配置"
    )
    p.add_argument(
        "--platform", choices=["macos", "windows"], default=None, help="覆盖平台检测"
    )
    p.add_argument("--no-config", action="store_true", help="只检测，不修改任何配置")
    p.add_argument("--iface", metavar="NAME", help="指定调试网卡名（跳过交互选择）")
    p.add_argument("--list-ifaces", action="store_true", help="列出候选网卡后退出")
    p.add_argument("--report", metavar="PATH", help="Markdown 报告输出路径")
    p.add_argument(
        "--net", default="192.168.100", help="调试网段前缀(默认 192.168.100)"
    )
    p.add_argument("--pc-ip", default=None, help="PC 端 IP(默认 <net>.1)")
    p.add_argument("--reg-ip", default=None, help="收银机 IP(默认 <net>.2)")
    p.add_argument("--adb-port", type=int, default=5555)
    args = p.parse_args(argv)

    if args.list_ifaces:
        return list_ifaces(args.platform)

    net = args.net
    pc_ip = args.pc_ip or f"{net}.1"
    reg_ip = args.reg_ip or f"{net}.2"

    ctx = RunContext(
        platform=args.platform
        or ("windows" if sys.platform.startswith("win") else "macos"),
        debug_net=net,
        pc_ip=pc_ip,
        reg_ip=reg_ip,
        adb_port=args.adb_port,
    )
    full_mode = not args.no_config
    selector = make_selector(ctx, full_mode, args.iface)

    if args.no_config:
        Orchestrator(ctx, on_select_iface=selector).run_detect_only()
    else:
        Orchestrator(ctx, on_select_iface=selector).run()

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
