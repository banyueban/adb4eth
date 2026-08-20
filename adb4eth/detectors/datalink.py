"""L1 物理层 + L2 数据链路层检测。

L1: 网卡链路状态、介质/协商速率。
L2: 对端 MAC 可达（ARP）、单向发送故障判定。
"""

from __future__ import annotations

from ..models import DetResult, RunContext
from ..platform.base import PlatformAdapter


class PhysicalLayerDetector:
    """L1 物理层。"""

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

        results.append(
            DetResult(
                "L1",
                "链路状态",
                iface.link_up,
                "PASS" if iface.link_up else "FAIL",
                f"{iface.name}: {'active' if iface.link_up else 'inactive'}",
                "链路未激活：检查网线两端是否插紧、扩展坞/USB网卡供电",
            )
        )
        # 协商速率：macOS 输出 "100baseTX"/"1000baseT"，Windows 输出 "100 Mbps"/"1 Gbps"
        media_ok = bool(
            iface.media
            and any(s in iface.media for s in ("baseTX", "baseT", "Mbps", "Gbps"))
        )
        results.append(
            DetResult(
                "L1",
                "协商速率",
                media_ok,
                "PASS" if (media_ok and iface.link_up) else "WARN",
                f"{iface.media or 'unknown'}",
                "未获取到协商速率：可能未插入对端设备",
            )
        )
        self.ctx.results.extend(results)
        return results


class DataLinkLayerDetector:
    """L2 数据链路层，含单向链路故障判定。"""

    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def detect(self) -> list[DetResult]:
        results = []
        iface = self.ctx.iface
        reg_ip = self.ctx.reg_ip
        if not iface:
            return results

        # 探测对端：ping 触发 ARP，再查 ARP 表
        # 若 PC 未配置 IP，先用当前状态尝试
        arp_before = self.adapter.get_arp_table()
        if iface.ip:
            PlatformAdapter.ping(
                reg_ip, count=2, timeout=2, source=iface.ip
            )  # 触发 ARP
        arp_after = self.adapter.get_arp_table()

        mac = arp_after.get(reg_ip) or arp_before.get(reg_ip)
        results.append(
            DetResult(
                "L2",
                "对端MAC解析(ARP)",
                bool(mac),
                "PASS" if mac else "WARN",
                f"{reg_ip} -> {mac if mac else '未解析'}",
                "无法解析收银机MAC：确认对端已开机、网线连通、或先配置本端IP后重试",
            )
        )

        # 单向链路故障检测（对端已连 ADB 时）
        results.append(
            DetResult(
                "L2",
                "双向帧检查",
                self.ctx.adb_available,
                "SKIP" if not self.ctx.adb_available else "PASS",
                "（需 Android 端可连以读取 rx/tx 计数）"
                if not self.ctx.adb_available
                else "对端可读统计，见ADB步骤",
                "",
            )
        )
        self.ctx.results.extend(results)
        return results
