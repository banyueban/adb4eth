"""macOS 平台适配器。

基于本次实测验证过的命令：
- networksetup（服务/接口映射、IP 配置、服务启停、服务顺序）
- ifconfig（链路/介质/ARP 状态）
- netstat / route（默认路由、路由表）
- networksetup -listallhardwareports（USB 网卡识别）
- nc（端口探测）
"""

from __future__ import annotations

import contextlib
import re

from ..models import NetIface
from .base import PlatformAdapter, run_cmd

# Realtek / ASIX / 常见 USB 网卡芯片 vendor 关键字
USB_NIC_VENDORS = ("0bda", "0b95", "17ef", "056e", "046d", "0e0f", "20a9")

_VIRTUAL_IFACE_RE = re.compile(
    r"^(gif|stf|utun|bridge|anpi|ap|awdl|llw|en[0-9]*ip|tun|tap|ppp|vlan|bond|lo)"
)


class MacOSAdapter(PlatformAdapter):
    platform = "macos"

    def _bridge_members(self) -> set:
        """Thunderbolt 桥接成员接口名集合（en1/en2 等，非物理网卡）。"""
        members = set()
        out = run_cmd(["ifconfig", "bridge0"])
        for m in re.finditer(r"^\s*member:\s+(\S+)", out, re.M):
            members.add(m.group(1))
        return members

    @staticmethod
    def _is_locally_administered(mac: str) -> bool:
        """本机管理 MAC（02:xx / 2a:xx / 36:xx 等），Thunderbolt 虚拟网口特征。"""
        if not mac or ":" not in mac:
            return False
        try:
            first = int(mac.split(":")[0], 16)
            return bool(first & 0x02)
        except ValueError:
            return True

    # ---------------------------------------------------------------
    def _service_iface_map(self) -> dict:
        """{服务名: 接口名}，来自 networksetup -listnetworkserviceorder。
        格式为两行：
            (1) Wi-Fi
            (Hardware Port: Wi-Fi, Device: en0)
        """
        out = run_cmd(["networksetup", "-listnetworkserviceorder"])
        mapping = {}
        lines = out.splitlines()
        i = 0
        while i < len(lines):
            m = re.search(r'^\((?:\*?\d+)\)\s+"?([^"]+)"?', lines[i])
            if m:
                svc = m.group(1).strip()
                # 下一行通常是 (Hardware Port: X, Device: Y)
                if i + 1 < len(lines):
                    dev_m = re.search(r"Device:\s*([a-zA-Z0-9]+)", lines[i + 1])
                    if dev_m:
                        mapping[svc] = dev_m.group(1)
            i += 1
        return mapping

    def list_interfaces(self) -> list[NetIface]:
        ifaces = []
        # 1) 接口集合
        out = run_cmd(["ifconfig", "-l"])
        names = out.split()
        # 2) 服务名映射
        svc_map = self._service_iface_map()
        # 3) Thunderbolt 桥成员（虚拟）
        bridge_members = self._bridge_members()
        # 4) USB 网卡信息（vendor / product 名）
        usb_names = self._usb_nic_names()

        for name in names:
            if _VIRTUAL_IFACE_RE.match(name):
                continue
            if name in bridge_members:
                continue
            detail = run_cmd(["ifconfig", name])
            # 本机管理 MAC => 虚拟网口（Thunderbolt/随机地址），排除
            mac_m = re.search(r"ether\s+([0-9a-f:]{10,17})", detail)
            if mac_m and self._is_locally_administered(mac_m.group(1)):
                continue
            iftype = "ethernet"
            # 服务名 -> 接口名 反查：找 name 对应哪个服务
            svc_name = next((s for s, d in svc_map.items() if d == name), "")
            if "Wi-Fi" in svc_name or "wifi" in svc_name.lower():
                iftype = "wifi"
            st = re.search(r"status:\s*(\w+)", detail)
            link_up = bool(st and st.group(1) == "active")
            media_m = re.search(r"media:\s*([^\n]*)", detail)
            media = media_m.group(1).strip() if media_m else None
            iface = NetIface(
                name=name,
                service=svc_name or None,
                iftype=iftype,
                link_up=link_up,
                media=media,
                vendor=usb_names.get(name),
            )
            iface = self._fill_ip(iface, detail)
            if iface.vendor:
                iface.is_usb = True
                iface.iftype = "usb_ethernet"
            ifaces.append(iface)
        return ifaces

    def _usb_nic_names(self) -> dict:
        """识别 USB 网卡。用 networksetup -listallhardwareports：
        Hardware Port 名含 "USB"/"LAN"/"Ethernet" 且 Device 为 enX 的，视为 USB 网卡。
        返回 {BSD接口名: 端口名}。"""
        res = {}
        out = run_cmd(["networksetup", "-listallhardwareports"])
        port = None
        for line in out.splitlines():
            m = re.search(r"^Hardware Port:\s*(.+)$", line)
            if m:
                port = m.group(1).strip()
                continue
            m = re.search(r"^Device:\s*(\S+)$", line)
            if m and port:
                dev = m.group(1)
                if re.search(r"USB|LAN", port, re.I) and re.match(r"^en\d+$", dev):
                    res[dev] = port
                port = None
        return res

    def _fill_ip(self, iface: NetIface, detail: str) -> NetIface:
        ip_m = re.search(
            r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+0x([0-9a-fA-F]+)", detail
        )
        if ip_m:
            iface.ip = ip_m.group(1)
            hexmask = ip_m.group(2)
            with contextlib.suppress(ValueError):
                iface.mask = ".".join(
                    str(int(hexmask[i : i + 2], 16)) for i in (0, 2, 4, 6)
                )
        return iface

    # ---------------------------------------------------------------
    def get_default_route_iface(self) -> str | None:
        out = run_cmd(["netstat", "-rn", "-f", "inet"])
        for line in out.splitlines():
            m = re.match(r"^default\s+\S+\s+\S+\s+(\S+)", line)
            if m:
                return m.group(1)
        return None

    def refresh_iface(self, iface: NetIface) -> NetIface:
        detail = run_cmd(["ifconfig", iface.name])
        st = re.search(r"status:\s*(\w+)", detail)
        iface.link_up = bool(st and st.group(1) == "active")
        media_m = re.search(r"media:\s*([^\n]*)", detail)
        iface.media = media_m.group(1).strip() if media_m else iface.media
        iface = self._fill_ip(iface, detail)
        # gateway：从路由表找该接口的 default
        gw = self._iface_gateway(iface.name)
        iface.gateway = gw
        return iface

    def _iface_gateway(self, name: str) -> str | None:
        out = run_cmd(["netstat", "-rn", "-f", "inet"])
        for line in out.splitlines():
            m = re.match(r"^default\s+(\S+)\s+\S+\s+\S+.*\s" + name + r"\b", line)
            if m:
                return m.group(1)
        return None

    def get_arp_table(self) -> dict:
        out = run_cmd(["arp", "-an"])
        res = {}
        for line in out.splitlines():
            m = re.search(
                r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]{10,17})\s+on\s+(\S+)", line
            )
            if m and not m.group(2).startswith("ff:ff"):
                res[m.group(1)] = m.group(2)
        return res

    def probe_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        try:
            g = int(timeout)
        except (TypeError, ValueError):
            g = 3
        out = run_cmd(
            ["nc", "-z", "-G", str(g), "-w", str(g), host, str(port)],
            timeout=timeout + 2,
        )
        return out == "" or "succeeded" in out

    # ---------------------------------------------------------------
    def _find_service(self, iface: NetIface) -> str:
        if iface.service:
            return iface.service
        svc_map = self._service_iface_map()
        for svc, dev in svc_map.items():
            if dev == iface.name:
                return svc
        raise RuntimeError(f"无法找到接口 {iface.name} 对应的网络服务名")

    def enable_iface(self, iface: NetIface) -> bool:
        svc = self._find_service(iface)
        cur = run_cmd(["networksetup", "-getnetworkserviceenabled", svc])
        if "Disabled" in cur:
            run_cmd(
                ["networksetup", "-setnetworkserviceenabled", svc, "on"], timeout=20
            )
            return True
        return False

    def set_static_ip_no_gw(self, iface: NetIface, ip: str, mask: str) -> bool:
        """networksetup -setmanual 服务名 IP 掩码 0.0.0.0 —— 网关 0.0.0.0 不产生默认路由。"""
        svc = self._find_service(iface)
        run_cmd(
            ["networksetup", "-setmanual", svc, ip, mask, "0.0.0.0"],
            timeout=30,
            check=True,
        )
        return True

    def snapshot_iface(self, iface: NetIface) -> dict:
        svc = self._find_service(iface)
        out = run_cmd(["networksetup", "-getinfo", svc])
        snap = {
            "svc": svc,
            "ip": None,
            "mask": None,
            "gw": None,
            "enabled": True,
            "cfg_type": None,
        }
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                if k.strip() == "IP address" and v:
                    snap["ip"] = v
                elif k.strip() == "Subnet mask" and v:
                    snap["mask"] = v
                elif k.strip() == "Router" and v:
                    snap["gw"] = v
        snap["enabled"] = "Disabled" not in run_cmd(
            ["networksetup", "-getnetworkserviceenabled", svc]
        )
        return snap

    def rollback_iface(self, iface: NetIface, snap: dict) -> bool:
        svc = snap.get("svc") or self._find_service(iface)
        try:
            if snap.get("ip"):
                run_cmd(
                    [
                        "networksetup",
                        "-setmanual",
                        svc,
                        snap["ip"],
                        snap.get("mask") or "255.255.255.0",
                        snap.get("gw") or "0.0.0.0",
                    ],
                    timeout=30,
                )
            else:
                run_cmd(["networksetup", "-setdhcp", svc], timeout=30)
            if not snap.get("enabled", True):
                run_cmd(
                    ["networksetup", "-setnetworkserviceenabled", svc, "off"],
                    timeout=20,
                )
            return True
        except Exception:
            return False

    def ensure_priority(self, protect_iface: str, iface: NetIface) -> bool:
        """把 protect_iface（上网网卡）的服务排到调试网卡之前。

        用 networksetup -ordernetworkservices 需要列出全部服务。这里取现有服务顺序，
        把 protect 的服务提到最前（或至少早于调试网卡）。
        """
        order = self._current_service_order()
        if not order:
            return False
        # 找 protect 服务名
        svc_map = self._service_iface_map()
        protect_svc = None
        debug_svc = self._find_service(iface)
        for svc, dev in svc_map.items():
            if dev == protect_iface:
                protect_svc = svc
                break
        if not protect_svc:
            return False
        if protect_svc == debug_svc:
            return True
        # 重排：protect_svc 移到 debug_svc 之前
        idx = order.index(debug_svc) if debug_svc in order else -1
        if protect_svc in order and (idx < 0 or order.index(protect_svc) < idx):
            # 已经在前面，无需改动
            return True
        new_order = []
        inserted = False
        for s in order:
            if s == protect_svc:
                continue
            if s == debug_svc and not inserted:
                new_order.append(protect_svc)
                inserted = True
            new_order.append(s)
        if not inserted:
            new_order.append(protect_svc)
        run_cmd(["networksetup", "-ordernetworkservices"] + new_order, timeout=30)
        return True

    def _current_service_order(self) -> list[str]:
        out = run_cmd(["networksetup", "-listnetworkserviceorder"])
        order = []
        for line in out.splitlines():
            m = re.search(r'^\((\d+)\)\s+"?([^"]+)"?', line)
            if m:
                order.append(m.group(2))
        return order
