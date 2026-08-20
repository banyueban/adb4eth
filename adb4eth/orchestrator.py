"""编排器：按阶段驱动整个流程，维护运行状态与结果汇总。"""

from __future__ import annotations

import contextlib

from .configurators.android_config import AndroidConfigurator
from .configurators.pc_config import PcConfigurator
from .detectors.adb import AdbDetector, TransportLayerDetector
from .detectors.datalink import DataLinkLayerDetector, PhysicalLayerDetector
from .detectors.network import NetworkLayerDetector
from .detectors.topology import TopologyDetector
from .models import DetResult, RunContext
from .platform.base import PlatformAdapter, create_adapter, run_cmd


class Orchestrator:
    def __init__(
        self, ctx: RunContext | None = None, on_stage=None, on_select_iface=None
    ):
        self.ctx = ctx or RunContext()
        self.adapter: PlatformAdapter = create_adapter(self.ctx.platform)
        self.on_stage = on_stage  # 可选阶段回调(phase, msg) -> None，供 GUI 实时显示
        self.on_select_iface = (
            on_select_iface  # 可选网卡选择回调(candidates) -> NetIface|None
        )

    def _emit(self, phase: str, msg: str) -> None:
        import time

        entry = {"phase": phase, "msg": msg, "ts": time.strftime("%H:%M:%S")}
        self.ctx.steps.append(entry)
        if self.on_stage:
            with contextlib.suppress(Exception):
                self.on_stage(phase, msg)

    def _find_connected_adb(self) -> str | None:
        """返回已连接的 ADB 序列号（用于操作收银机端），无则 None。"""
        out = run_cmd(["adb", "devices"], timeout=10)
        for line in out.splitlines():
            parts = line.split()
            if (
                len(parts) >= 2
                and parts[1] == "device"
                and not parts[0].startswith("*")
            ):
                return parts[0]
        return None

    def run(self) -> RunContext:
        ctx = self.ctx
        # 阶段0: 发现已连 ADB（Wi-Fi 通道）以便操作 Android 端
        self._emit("ADB", "检测已连接的 ADB 通道（Wi-Fi）…")
        self.ctx.adb_available = bool(self._find_connected_adb())

        # 阶段1: 拓扑
        self._emit("拓扑", "枚举网卡、识别默认路由与 USB 网卡…")
        TopologyDetector(ctx, self.adapter, selector=self.on_select_iface).detect()
        if not ctx.iface:
            self._emit("拓扑", "未找到可用的有线调试网卡")
            return ctx

        # 阶段2: L1/L2
        self._emit("L1/L2", f"检测链路状态（{ctx.iface.name}）…")
        PhysicalLayerDetector(ctx, self.adapter).detect()
        DataLinkLayerDetector(ctx, self.adapter).detect()

        # 阶段3: PC 配置（若本端无 IP 或 IP 不在调试网段）
        fresh = self.adapter.refresh_iface(ctx.iface)
        ctx.iface = fresh
        need_cfg = not (fresh.ip and fresh.ip.startswith(ctx.debug_net))
        if need_cfg:
            self._emit(
                "配置", f"为 {ctx.iface.name} 配置静态 IP {ctx.pc_ip}（不设默认网关）…"
            )
            PcConfigurator(ctx, self.adapter).configure()
        else:
            self._emit("配置", f"{ctx.iface.name} 已在调试网段，跳过配置")

        # 阶段4: Android 配置（有已连 ADB 时自动，否则人工指引）
        serial = self._find_connected_adb()
        android_cfg = AndroidConfigurator(ctx)
        if serial:
            self._emit("收银机", f"通过 ADB({serial}) 自动配置收银机以太网…")
            android_cfg.set_serial(serial)
            android_cfg.configure()
        else:
            self._emit("收银机", "无已连 ADB 通道，需人工在收银机配置")
            ctx.results.append(
                DetResult(
                    "ANDROID",
                    "收银机配置",
                    False,
                    "WARN",
                    "无已连ADB通道，需人工在收银机配置",
                    "在收银机：设置→以太网→静态IP "
                    f"{ctx.reg_ip}/255.255.255.0；开发者选项→打开「网络调试」",
                )
            )

        # 阶段5: L3 网络层
        self._emit("L3", "网络层检测：IP/路由/默认路由保护…")
        NetworkLayerDetector(ctx, self.adapter).detect()

        # 阶段6: L4 传输层
        self._emit("L4", f"探测收银机 {ctx.reg_ip}:{ctx.adb_port} …")
        TransportLayerDetector(ctx, self.adapter).detect()

        # 阶段7: L7 ADB 连接
        self._emit("ADB", f"adb connect {ctx.reg_ip}:{ctx.adb_port} …")
        AdbDetector(ctx, self.adapter).detect()

        return ctx

    def run_detect_only(self) -> RunContext:
        """只检测不配置：拓扑 → L1 → L3 → L4 → L7，GUI「仅检测」模式复用。"""
        ctx = self.ctx
        self._emit("ADB", "检测已连接的 ADB 通道…")
        ctx.adb_available = bool(self._find_connected_adb())

        self._emit("拓扑", "枚举网卡、识别默认路由与 USB 网卡…")
        TopologyDetector(ctx, self.adapter, selector=self.on_select_iface).detect()
        if not ctx.iface:
            self._emit("拓扑", "未找到可用的有线调试网卡")
            return ctx

        self._emit("L1/L2", f"检测链路状态（{ctx.iface.name}）…")
        PhysicalLayerDetector(ctx, self.adapter).detect()
        DataLinkLayerDetector(ctx, self.adapter).detect()

        fresh = self.adapter.refresh_iface(ctx.iface)
        ctx.iface = fresh

        self._emit("L3", "网络层检测：IP/路由/默认路由保护…")
        NetworkLayerDetector(ctx, self.adapter).detect()

        self._emit("L4", f"探测收银机 {ctx.reg_ip}:{ctx.adb_port} …")
        TransportLayerDetector(ctx, self.adapter).detect()

        self._emit("ADB", f"adb connect {ctx.reg_ip}:{ctx.adb_port} …")
        AdbDetector(ctx, self.adapter).detect()

        return ctx
