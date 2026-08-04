# -*- coding: utf-8 -*-
"""公共数据结构定义。"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NetIface:
    """网络接口信息。"""
    name: str                      # 接口名：en11 / 以太网 / ifIndex
    service: Optional[str] = None  # macOS 网络服务名（networksetup 用）
    iftype: str = "unknown"        # ethernet / usb_ethernet / wifi / loopback / virtual
    ip: Optional[str] = None
    mask: Optional[str] = None
    gateway: Optional[str] = None
    link_up: bool = False
    media: Optional[str] = None    # 100baseTX / 1 Gbps
    vendor: Optional[str] = None   # RTL8153 / ASIX / Realtek
    is_usb: bool = False

    @property
    def is_physical(self) -> bool:
        return self.iftype in ("ethernet", "usb_ethernet", "wifi")


@dataclass
class DetResult:
    """单层检测结果。"""
    layer: str        # L1..L7 / CFG / ADB
    name: str
    ok: bool
    status: str = "PASS"      # PASS / FAIL / WARN / SKIP
    evidence: str = ""        # 命令 + 关键输出
    advice: str = ""          # 失败时的处理建议
    detail: str = ""

    def to_row(self) -> dict:
        return {
            "layer": self.layer,
            "name": self.name,
            "status": self.status,
            "ok": self.ok,
            "evidence": self.evidence,
            "advice": self.advice,
            "detail": self.detail,
        }


@dataclass
class Snapshot:
    """变更前配置快照，用于回滚。"""
    iface_cfg: dict = field(default_factory=dict)       # name -> {ip,mask,gateway,enabled,cfg_type}
    service_order: list = field(default_factory=list)   # macOS 服务顺序
    default_route_iface: Optional[str] = None


@dataclass
class AdbState:
    host: str
    port: int = 5555
    status: str = "not_connected"   # device/offline/unauthorized/not_connected
    model: Optional[str] = None


@dataclass
class RunContext:
    """贯穿整个运行过程的共享状态。"""
    platform: str = "macos"         # macos / windows
    iface: Optional[NetIface] = None       # 选定的调试网卡
    default_route_iface: Optional[str] = None
    debug_net: str = "192.168.100"  # 调试网段
    pc_ip: str = "192.168.100.1"
    reg_ip: str = "192.168.100.2"
    mask: str = "255.255.255.0"
    adb_port: int = 5555
    results: list = field(default_factory=list)
    steps: list = field(default_factory=list)   # 阶段进度日志: {phase, msg, ts}
    snapshot: Optional[Snapshot] = None
    adb: Optional[AdbState] = None
    adb_available: bool = False     # 是否有已连的 ADB 通道（Wi-Fi）可操作 Android 端


def mask_to_prefix(mask: str) -> int:
    """255.255.255.0 -> 24"""
    if "/" in mask:
        return int(mask.split("/")[1])
    parts = [int(x) for x in mask.split(".")]
    bits = 0
    for p in parts:
        bits += bin(p).count("1")
    return bits
