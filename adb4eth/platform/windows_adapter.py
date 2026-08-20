"""Windows 平台适配器。

基于 PowerShell / netsh：
- Get-NetAdapter（网卡、PnPDeviceID、链路、速率；排除虚拟网卡）
- Get-NetIPAddress / Get-NetIPConfiguration（接口地址、网关）
- netsh interface ip set address（设静态 IP，不设默认网关；回滚 DHCP）
- arp -a / .NET TcpClient（端口探测）

修复要点（相对旧实现）：
- USB 网卡按 PnPDeviceID 判定，不再把内置 Realtek PCIe 网卡误判为 USB。
- _prefix_to_mask 字节序正确（旧实现 /24 会得到 0.255.255.255）。
- 变更类命令 check=True，失败抛 CommandError，不再“静默成功”。
- PowerShell 输出强制 UTF-8，中文网卡名不乱码。
- 端口探测用 TcpClient 超时，避免 Test-NetConnection 长时间卡死。
- 快照记录 DHCP/静态来源，回滚可还原 DHCP。
"""

from __future__ import annotations

import json
import re

from ..models import NetIface, mask_to_prefix
from .base import PlatformAdapter, run_cmd

_PS_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"


class WindowsAdapter(PlatformAdapter):
    platform = "windows"

    def _run_ps(self, script: str, timeout: float = 20.0, check: bool = False) -> str:
        return run_cmd(
            ["powershell", "-NoProfile", "-Command", _PS_PREFIX + script],
            timeout=timeout,
            check=check,
            encoding="utf-8",
        )

    @staticmethod
    def _load_json(out: str):
        try:
            return json.loads(out.strip())
        except ValueError:
            return None

    @staticmethod
    def _int_or(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ---------------------------------------------------------------
    # 只读检测
    # ---------------------------------------------------------------
    def list_interfaces(self) -> list[NetIface]:
        out = self._run_ps(
            "Get-NetAdapter | "
            "Where-Object { -not $_.Virtual -and $_.Status -in @('Up','Disconnected','Down') } | "
            "Select-Object Name,InterfaceDescription,Status,LinkSpeed,InterfaceIndex,PnPDeviceID | "
            "ConvertTo-Json -Compress"
        )
        data = self._load_json(out)
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        ifaces = []
        for item in data or []:
            desc = item.get("InterfaceDescription") or ""
            status = item.get("Status") or ""
            name = item.get("Name") or str(item.get("InterfaceIndex") or "")
            pnp = item.get("PnPDeviceID") or ""
            iftype = "ethernet"
            if re.search(r"(Wi-?Fi|Wireless)", desc, re.I):
                iftype = "wifi"
            elif self._is_usb_device(pnp, desc):
                iftype = "usb_ethernet"
            iface = NetIface(
                name=name,
                iftype=iftype,
                link_up=(status.lower() == "up"),
                media=item.get("LinkSpeed") or None,
                vendor=desc,
                is_usb=(iftype == "usb_ethernet"),
            )
            self._fill_ip(iface)
            ifaces.append(iface)
        return ifaces

    @staticmethod
    def _is_usb_device(pnp_id: str, desc: str) -> bool:
        """USB 网卡判定：PnPDeviceID 以 USB\\ 开头最可靠，描述含 USB 关键字兜底。"""
        if pnp_id:
            upper = pnp_id.upper()
            if upper.startswith("USB\\"):
                return True
            if "USB" in upper and "PCI" not in upper:
                return True
        # 兜底：描述里有 USB 网卡特征词，但排除 PCIe 内置网卡
        return bool(
            re.search(r"(USB|ASIX|AX88|RTL815|RTL881)", desc, re.I)
        ) and not re.search(r"PCIe|PCI\b", desc, re.I)

    def _fill_ip(self, iface: NetIface) -> None:
        out = self._run_ps(
            f"Get-NetIPAddress -InterfaceAlias '{iface.name}' -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 IPAddress,PrefixLength | ConvertTo-Json -Compress",
            timeout=15,
        )
        data = self._load_json(out)
        if data is None:
            return
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            iface.ip = item.get("IPAddress")
            plen = item.get("PrefixLength")
            if plen is not None:
                iface.mask = self._prefix_to_mask(self._int_or(plen, 24))
            break

    @staticmethod
    def _prefix_to_mask(prefix: int) -> str:
        """前缀长度 -> 点分掩码。旧实现字节序颠倒（/24 得 0.255.255.255）。"""
        mask32 = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        return ".".join(str((mask32 >> (24 - 8 * i)) & 0xFF) for i in range(4))

    def get_default_route_iface(self) -> str | None:
        out = self._run_ps(
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
            "Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1 ifIndex | "
            "ConvertTo-Json -Compress"
        )
        data = self._load_json(out)
        idx = data.get("ifIndex") if isinstance(data, dict) else None
        if idx is None:
            return None
        try:
            out2 = self._run_ps(
                f"Get-NetAdapter -InterfaceIndex {idx} -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty Name"
            )
            return out2.strip() or None
        except Exception:
            return None

    def refresh_iface(self, iface: NetIface) -> NetIface:
        out = self._run_ps(
            f"Get-NetAdapter -InterfaceAlias '{iface.name}' | "
            "Select-Object Status,LinkSpeed | ConvertTo-Json -Compress"
        )
        d = self._load_json(out) or {}
        iface.link_up = (d.get("Status") or "").lower() == "up"
        iface.media = d.get("LinkSpeed") or iface.media
        self._fill_ip(iface)
        iface.gateway = self._iface_gateway(iface.name)
        return iface

    def _iface_gateway(self, name: str) -> str | None:
        out = self._run_ps(
            f"$cfg = Get-NetIPConfiguration -InterfaceAlias '{name}' -ErrorAction SilentlyContinue; "
            "if ($cfg) { $g = $cfg | Select-Object -ExpandProperty IPv4DefaultGateway -ErrorAction SilentlyContinue; "
            "if ($g) { $g | Select-Object -First 1 -ExpandProperty NextHop } }"
        )
        return out.strip() or None

    def get_arp_table(self) -> dict:
        out = run_cmd(["arp", "-a"], timeout=15)
        res = {}
        for line in out.splitlines():
            m = re.search(
                r"(\d+\.\d+\.\d+\.\d+)\s+"
                r"([0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2})",
                line,
            )
            if m and not m.group(2).startswith("ff-"):
                res[m.group(1)] = m.group(2).replace("-", ":")
        return res

    def probe_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        ms = max(1, self._int_or(timeout * 1000, 3000))
        script = (
            f"$c = New-Object System.Net.Sockets.TcpClient; "
            f"try {{ $iar = $c.BeginConnect('{host}', {port}, $null, $null); "
            f"$ok = $iar.AsyncWaitHandle.WaitOne({ms}, $false) -and $c.Connected; "
            f"if ($ok) {{ $c.EndConnect($iar) }}; if ($ok) {{ 'True' }} else {{ 'False' }} }} "
            f"finally {{ $c.Close() }}"
        )
        out = self._run_ps(script, timeout=timeout + 5)
        return out.strip().endswith("True")

    def is_admin(self) -> bool:
        out = self._run_ps(
            "$p = New-Object Security.Principal.WindowsPrincipal("
            "[Security.Principal.WindowsIdentity]::GetCurrent()); "
            "$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
            timeout=15,
        )
        return out.strip().endswith("True")

    # ---------------------------------------------------------------
    # 配置（写操作，需管理员权限；失败抛 CommandError）
    # ---------------------------------------------------------------
    def enable_iface(self, iface: NetIface) -> bool:
        self._run_ps(
            f"$a = Get-NetAdapter -InterfaceAlias '{iface.name}' -ErrorAction SilentlyContinue; "
            "if (-not $a) { throw 'adapter not found' }; "
            "if ($a.Status -ne 'Up') { Enable-NetAdapter -InputObject $a -Confirm:$false }",
            timeout=30,
            check=True,
        )
        return True

    def set_static_ip_no_gw(self, iface: NetIface, ip: str, mask: str) -> bool:
        """netsh 原子替换地址：static <ip> <mask> none（none = 不设默认网关）。"""
        run_cmd(
            [
                "netsh",
                "interface",
                "ip",
                "set",
                "address",
                f'name="{iface.name}"',
                "static",
                ip,
                mask,
                "none",
            ],
            timeout=30,
            check=True,
        )
        return True

    def snapshot_iface(self, iface: NetIface) -> dict:
        script = (
            f"$ips = Get-NetIPAddress -InterfaceAlias '{iface.name}' -AddressFamily IPv4 "
            "-ErrorAction SilentlyContinue | Select-Object IPAddress,PrefixLength,PrefixOrigin; "
            f"$cfg = Get-NetIPConfiguration -InterfaceAlias '{iface.name}' -ErrorAction SilentlyContinue; "
            "$gw = $null; "
            "if ($cfg) { $g = $cfg | Select-Object -ExpandProperty IPv4DefaultGateway -ErrorAction SilentlyContinue; "
            "if ($g) { $gw = $g | Select-Object -First 1 -ExpandProperty NextHop } }; "
            "@{ ips = @($ips | ForEach-Object { "
            "@{ ip = $_.IPAddress; prefix = $_.PrefixLength; dhcp = ($_.PrefixOrigin -eq 'Dhcp') } }); "
            "gw = $gw } | ConvertTo-Json -Compress"
        )
        out = self._run_ps(script, timeout=30)
        snap = {
            "ip": iface.ip,
            "mask": iface.mask,
            "gw": iface.gateway,
            "dhcp": False,
            "addresses": [],
        }
        data = self._load_json(out)
        if isinstance(data, dict):
            addrs = data.get("ips") or []
            if isinstance(addrs, dict):
                addrs = [addrs]
            for a in addrs or []:
                aip = a.get("ip")
                if aip:
                    snap["addresses"].append(
                        {
                            "ip": aip,
                            "prefix": a.get("prefix"),
                            "dhcp": bool(a.get("dhcp")),
                        }
                    )
            gw = data.get("gw")
            if gw:
                snap["gw"] = gw
            if snap["addresses"]:
                first = snap["addresses"][0]
                snap["ip"] = first["ip"]
                if first.get("prefix") is not None:
                    snap["mask"] = self._prefix_to_mask(
                        self._int_or(first["prefix"], 24)
                    )
                snap["dhcp"] = first["dhcp"]
        return snap

    def rollback_iface(self, iface: NetIface, snap: dict) -> bool:
        name = iface.name
        try:
            addrs = [a for a in (snap.get("addresses") or []) if a.get("ip")]
            if not addrs and snap.get("ip"):
                addrs = [
                    {
                        "ip": snap["ip"],
                        "prefix": mask_to_prefix(snap.get("mask") or "255.255.255.0"),
                        "dhcp": bool(snap.get("dhcp")),
                    }
                ]
            # 原为 DHCP：直接恢复自动获取
            if addrs and all(a.get("dhcp") for a in addrs):
                run_cmd(
                    [
                        "netsh",
                        "interface",
                        "ip",
                        "set",
                        "address",
                        f'name="{name}"',
                        "dhcp",
                    ],
                    timeout=30,
                    check=True,
                )
                return True
            # 原为静态：清空当前 IPv4（含 DHCP 残留）后按快照恢复
            self._run_ps(
                f"Get-NetIPAddress -InterfaceAlias '{name}' -AddressFamily IPv4 "
                "-ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false "
                "-ErrorAction SilentlyContinue",
                timeout=30,
                check=True,
            )
            if addrs:
                first = addrs[0]
                first_mask = (
                    self._prefix_to_mask(int(first["prefix"]))
                    if first.get("prefix") is not None
                    else (snap.get("mask") or "255.255.255.0")
                )
                gw = snap.get("gw") or "none"
                run_cmd(
                    [
                        "netsh",
                        "interface",
                        "ip",
                        "set",
                        "address",
                        f'name="{name}"',
                        "static",
                        first["ip"],
                        first_mask,
                        gw,
                    ],
                    timeout=30,
                    check=True,
                )
                for a in addrs[1:]:
                    m = (
                        self._prefix_to_mask(int(a["prefix"]))
                        if a.get("prefix") is not None
                        else "255.255.255.0"
                    )
                    run_cmd(
                        [
                            "netsh",
                            "interface",
                            "ip",
                            "add",
                            "address",
                            f'name="{name}"',
                            a["ip"],
                            m,
                        ],
                        timeout=30,
                        check=True,
                    )
            return True
        except Exception:
            return False

    def ensure_priority(self, protect_iface: str, iface: NetIface) -> bool:
        return True  # Windows 下通过“调试网卡不设网关”保证，无需调优先级
