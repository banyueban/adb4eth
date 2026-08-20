"""L3 网络层检测：本端 IP/掩码、对端可达、路由、默认路由保护回验。"""

from __future__ import annotations

from ..models import DetResult, RunContext
from ..platform.base import PlatformAdapter, run_cmd

_PS_UTF8 = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"


class NetworkLayerDetector:
    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def detect(self) -> list[DetResult]:
        results = []
        iface = self.ctx.iface
        if not iface:
            return results

        iface = self.adapter.refresh_iface(iface)
        self.ctx.iface = iface

        # 本端 IP/掩码
        results.append(
            DetResult(
                "L3",
                "本端IP配置",
                bool(iface.ip),
                "PASS" if iface.ip else "FAIL",
                f"{iface.name}: {iface.ip or '(none)'}/{iface.mask or '?'}",
                "本端无IP：运行配置步骤配置静态IP",
            )
        )

        # 对端 ping（绑定源 IP）
        if iface.ip:
            ok = PlatformAdapter.ping(
                self.ctx.reg_ip, count=3, timeout=3, source=iface.ip
            )
            # 收银机常不响应 ICMP，但 ADB(TCP) 可通；此时记 WARN 而非 FAIL
            results.append(
                DetResult(
                    "L3",
                    "对端可达(ping)",
                    ok,
                    "PASS" if ok else "WARN",
                    f"ping -S {iface.ip} {self.ctx.reg_ip}",
                    "对端不响应ICMP属正常（部分收银机ROM），以ADB端口为准；若ADB也不通则检查链路",
                )
            )
        else:
            results.append(
                DetResult("L3", "对端可达(ping)", False, "SKIP", "未配置IP，跳过")
            )

        # 路由指向调试网卡
        route_ok = True
        route_evidence = iface.name
        if self.ctx.platform == "macos":
            out = run_cmd(["route", "-n", "get", self.ctx.reg_ip])
            if "interface:" in out:
                route_ok = f"interface: {iface.name}" in out
                route_evidence = out.split("interface:")[1].splitlines()[0].strip()
        else:
            rname = self._windows_route_iface()
            route_ok = rname == iface.name
            route_evidence = rname or "无路由"
        results.append(
            DetResult(
                "L3",
                "路由指向调试网卡",
                route_ok,
                "PASS" if route_ok else "FAIL",
                route_evidence,
                "路由未指向调试网卡：请检查网段配置",
            )
        )

        # 默认路由保护回验
        def_if = self.adapter.get_default_route_iface()
        protected = def_if == self.ctx.default_route_iface
        results.append(
            DetResult(
                "L3",
                "默认路由保护",
                protected,
                "PASS" if protected else "FAIL",
                f"默认路由: {def_if} (原: {self.ctx.default_route_iface})",
                "默认路由被改动！将自动回滚配置以保护联网",
            )
        )
        self.ctx.results.extend(results)
        return results

    def _windows_route_iface(self) -> str | None:
        """Windows：查目标 IP 的最佳出接口（Find-NetRoute），不可用时回退网段路由匹配。"""
        script = (
            "$r = $null; "
            "if (Get-Command Find-NetRoute -ErrorAction SilentlyContinue) { "
            f"$r = Find-NetRoute -RemoteIPAddress {self.ctx.reg_ip} -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 }; "
            "if (-not $r) { "
            f"$idx = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            f"Where-Object {{ $_.DestinationPrefix -like '{self.ctx.debug_net}.*' }} | "
            "Select-Object -First 1 -ExpandProperty InterfaceIndex; "
            "if ($idx) { (Get-NetAdapter -InterfaceIndex $idx -ErrorAction SilentlyContinue).Name } "
            "} else { (Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue).Name }"
        )
        out = run_cmd(
            ["powershell", "-NoProfile", "-Command", _PS_UTF8 + script],
            timeout=20,
            encoding="utf-8",
        )
        return out.strip() or None
