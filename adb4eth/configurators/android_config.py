# -*- coding: utf-8 -*-
"""Android 端配置器：通过已连 ADB（Wi-Fi 等）配置收银机以太网与 adbd。

注意：这些是运行时配置，重启会丢失；持久化需引导用户在系统设置→以太网配静态IP。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..models import DetResult, RunContext
from ..platform.base import run_cmd


class AndroidConfigurator:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.serial: Optional[str] = None  # 已连 ADB 序列号，用于在收银机上执行

    def _sh(self, cmd: str, timeout: float = 15.0) -> str:
        if not self.serial:
            return ""
        return run_cmd(["adb", "-s", self.serial, "shell", cmd], timeout=timeout)

    def set_serial(self, serial: str):
        self.serial = serial

    def configure(self) -> List[DetResult]:
        results = []
        if not self.serial:
            results.append(DetResult("ANDROID", "Android配置", False, "SKIP",
                                     "无已连ADB通道，需人工指引", ""))
            return results

        reg_ip = self.ctx.reg_ip
        mask_prefix = 24

        # 1. 检查 adb 端口
        port = self._sh("getprop service.adb.tcp.port").strip()
        if port != "5555":
            self._sh("setprop service.adb.tcp.port 5555")
            self._sh("stop adbd; start adbd")
            results.append(DetResult("ANDROID", "adbd端口", True, "PASS",
                                     "已设置 service.adb.tcp.port=5555 并重启adbd", ""))
        else:
            results.append(DetResult("ANDROID", "adbd端口", True, "PASS", "已是5555", ""))

        # 2. 找以太网接口
        out = self._sh("ip addr show")
        eth_ifaces = re.findall(r"^\d+:\s+(eth\S+):", out, re.M)
        if not eth_ifaces:
            results.append(DetResult("ANDROID", "以太网接口", False, "FAIL",
                                     "未找到 ethX 接口", "收银机可能无网口，或需检查硬件"))
            return results
        eth = eth_ifaces[0]

        # 3. 拉起接口 + 确认载波
        self._sh(f"ip link set {eth} up")
        carrier = self._sh(f"cat /sys/class/net/{eth}/carrier").strip()
        results.append(DetResult("ANDROID", "以太网载波", carrier == "1",
                                 "PASS" if carrier == "1" else "FAIL",
                                 f"{eth}: carrier={carrier}",
                                 "无载波：检查网线两端、对端PC链路"))

        # 4. 配 IP
        cur = self._sh(f"ip addr show {eth}")
        if reg_ip not in cur:
            self._sh(f"ip addr add {reg_ip}/{mask_prefix} dev {eth}")
        conf = self._sh(f"ip addr show {eth}")
        ok = reg_ip in conf
        results.append(DetResult("ANDROID", "以太网IP", ok,
                                 "PASS" if ok else "FAIL",
                                 f"{eth}: {reg_ip}/{mask_prefix}",
                                 "IP配置失败：尝试 ip addr add"))

        # 5. 路由
        route = self._sh("ip route show")
        net_ok = self.ctx.debug_net in route
        results.append(DetResult("ANDROID", "网段路由", net_ok,
                                 "PASS" if net_ok else "WARN",
                                 route.strip()[:120],
                                 "未出现目标网段路由"))

        self.ctx.results.extend(results)
        return results
