# -*- coding: utf-8 -*-
"""Windows 平台适配器。

基于 PowerShell / netsh：
- Get-NetAdapter / Get-NetIPConfiguration（网卡、IP、默认路由）
- Get-NetIPAddress（接口地址）
- New-NetIPAddress / netsh（设静态 IP，不设默认网关）
- arp -a / Test-NetConnection
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..models import NetIface, mask_to_prefix
from .base import PlatformAdapter, run_cmd, run_shell

_USB_NIC_PATTERN = re.compile(r"(Realtek|ASIX|RTL8|USB.*Ethernet|AX88|AQC|Marvell|Broadcom)", re.I)


class WindowsAdapter(PlatformAdapter):
    platform = "windows"

    def _run_ps(self, script: str, timeout: float = 20.0) -> str:
        return run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=timeout)

    def list_interfaces(self) -> List[NetIface]:
        out = self._run_ps(
            "Get-NetAdapter | "
            "Where-Object { $_.Status -in @('Up','Disconnected','Down') } | "
            "Select-Object Name,InterfaceDescription,Status,LinkSpeed,InterfaceIndex | "
            "ConvertTo-Json -Compress"
        )
        import json
        if not out.strip():
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = []
        if isinstance(data, dict):
            data = [data]
        ifaces = []
        for item in data:
            desc = item.get("InterfaceDescription") or ""
            status = item.get("Status") or ""
            name = item.get("Name") or str(item.get("InterfaceIndex") or "")
            iftype = "ethernet"
            if re.search(r"(Wi-?Fi|Wireless)", desc, re.I):
                iftype = "wifi"
            if _USB_NIC_PATTERN.search(desc):
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

    def _fill_ip(self, iface: NetIface) -> None:
        out = self._run_ps(
            f"Get-NetIPAddress -InterfaceAlias '{iface.name}' -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            "Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress", timeout=15)
        import json
        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            iface.ip = item.get("IPAddress")
            plen = item.get("PrefixLength")
            if plen is not None:
                iface.mask = self._prefix_to_mask(int(plen))
            break

    @staticmethod
    def _prefix_to_mask(prefix: int) -> str:
        return ".".join(str(((0xFFFFFFFF << (32 - prefix)) >> (8 * i)) & 0xFF) for i in range(4))

    def get_default_route_iface(self) -> Optional[str]:
        out = self._run_ps(
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 ifIndex | ConvertTo-Json -Compress")
        import json
        try:
            data = json.loads(out.strip())
            idx = data.get("ifIndex")
            if idx is None:
                return None
            out2 = self._run_ps(f"Get-NetAdapter -InterfaceIndex {idx} | Select-Object Name | ConvertTo-Json -Compress")
            d2 = json.loads(out2.strip())
            return d2.get("Name")
        except Exception:
            return None

    def refresh_iface(self, iface: NetIface) -> NetIface:
        out = self._run_ps(
            f"Get-NetAdapter -InterfaceAlias '{iface.name}' | "
            "Select-Object Status,LinkSpeed | ConvertTo-Json -Compress")
        import json
        try:
            d = json.loads(out.strip())
            iface.link_up = (d.get("Status") or "").lower() == "up"
            iface.media = d.get("LinkSpeed") or iface.media
        except Exception:
            pass
        self._fill_ip(iface)
        # gateway: 该网卡上的默认网关
        iface.gateway = self._iface_gateway(iface.name)
        return iface

    def _iface_gateway(self, name: str) -> Optional[str]:
        out = self._run_ps(
            f"Get-NetIPConfiguration -InterfaceAlias '{name}' -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty IPv4DefaultGateway | Select-Object -ExpandProperty NextHop")
        return out.strip() or None

    def get_arp_table(self) -> dict:
        out = run_shell("arp -a")
        res = {}
        for line in out.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2})", line)
            if m and not m.group(2).startswith("ff-"):
                mac = m.group(2).replace("-", ":")
                res[m.group(1)] = mac
        return res

    def probe_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        out = self._run_ps(
            f"(Test-NetConnection -ComputerName {host} -Port {port} -WarningAction SilentlyContinue).TcpTestSucceeded",
            timeout=timeout + 5)
        return out.strip() == "True"

    # ---------------------------------------------------------------
    def enable_iface(self, iface: NetIface) -> bool:
        out = self._run_ps(f"Enable-NetAdapter -InterfaceAlias '{iface.name}' -Confirm:$false")
        return True

    def set_static_ip_no_gw(self, iface: NetIface, ip: str, mask: str) -> bool:
        prefix = mask_to_prefix(mask)
        # 先移除该网卡现有默认网关（若错误存在），再加 IP（不加 -DefaultGateway）
        self._run_ps(
            f"Remove-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceAlias '{iface.name}' "
            f"-Confirm:$false -ErrorAction SilentlyContinue")
        out = self._run_ps(
            f"New-NetIPAddress -InterfaceAlias '{iface.name}' -IPAddress {ip} -PrefixLength {prefix} "
            f"-ErrorAction Stop")
        return True

    def snapshot_iface(self, iface: NetIface) -> dict:
        out = self._run_ps(
            f"Get-NetIPConfiguration -InterfaceAlias '{iface.name}' -ErrorAction SilentlyContinue | "
            "ConvertTo-Json -Compress")
        snap = {"ip": iface.ip, "mask": iface.mask, "gw": iface.gateway}
        try:
            import json
            data = json.loads(out.strip())
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                ip4 = data.get("IPv4Address")
                if ip4:
                    snap["ip"] = ip4.get("IPAddress")
                    plen = ip4.get("PrefixLength")
                    if plen is not None:
                        snap["mask"] = self._prefix_to_mask(int(plen))
                gw = data.get("IPv4DefaultGateway")
                if gw:
                    snap["gw"] = gw.get("NextHop")
        except Exception:
            pass
        return snap

    def rollback_iface(self, iface: NetIface, snap: dict) -> bool:
        try:
            self._run_ps(
                f"Remove-NetIPAddress -InterfaceAlias '{iface.name}' -Confirm:$false -ErrorAction SilentlyContinue")
            if snap.get("ip"):
                self._run_ps(
                    f"New-NetIPAddress -InterfaceAlias '{iface.name}' -IPAddress {snap['ip']} "
                    f"-PrefixLength {mask_to_prefix(snap.get('mask') or '255.255.255.0')} -ErrorAction Stop")
            if snap.get("gw"):
                self._run_ps(
                    f"New-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceAlias '{iface.name}' "
                    f"-NextHop {snap['gw']} -ErrorAction Stop")
            return True
        except Exception:
            return False

    def ensure_priority(self, protect_iface: str, iface: NetIface) -> bool:
        return True  # Windows 下通过"调试网卡不设网关"保证，无需调优先级
