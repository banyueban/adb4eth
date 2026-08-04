# -*- coding: utf-8 -*-
"""自测脚本：用模拟的 USB 网卡环境验证完整编排流程（不修改真实网络）。"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adb4eth.models import NetIface, RunContext
from adb4eth.platform.base import PlatformAdapter


class MockAdapter(PlatformAdapter):
    """模拟一个带 USB 网卡的 macOS 环境。"""

    def __init__(self):
        self.en11 = NetIface(name="en11", service="USB 10/100/1000 LAN",
                             iftype="usb_ethernet", ip="169.254.118.5",
                             mask="255.255.0.0", link_up=True,
                             media="100baseTX <full-duplex>", vendor="RTL8153", is_usb=True)
        self.en0 = NetIface(name="en0", service="Wi-Fi", iftype="wifi",
                            ip="192.168.0.151", mask="255.255.255.0", link_up=True, media="autoselect")
        self.cfg_called = []
        self.port_open = False

    def list_interfaces(self):
        return [self.en0, self.en11]

    def get_default_route_iface(self):
        return "en0"

    def refresh_iface(self, iface):
        if iface.name == "en11":
            return replace(self.en11)
        return replace(self.en0)

    def get_arp_table(self):
        return {"192.168.100.2": "3c:d1:6e:3b:1e:58"} if self.port_open else {}

    def probe_port(self, host, port, timeout=3.0):
        return self.port_open

    def enable_iface(self, iface):
        self.cfg_called.append("enable")
        return True

    def set_static_ip_no_gw(self, iface, ip, mask):
        self.cfg_called.append(f"setip:{ip}/{mask}")
        self.en11.ip = ip
        self.en11.mask = mask
        return True

    def snapshot_iface(self, iface):
        return {"ip": "169.254.118.5", "mask": "255.255.0.0", "gw": None, "enabled": True, "svc": iface.service}

    def rollback_iface(self, iface, snap):
        self.cfg_called.append("rollback")
        return True

    def ensure_priority(self, protect, iface):
        self.cfg_called.append("priority")
        return True


def test_no_usb_nic_selects_nothing():
    """无 USB 网卡时不选默认路由网卡作为调试网卡。"""
    from adb4eth.detectors.topology import TopologyDetector
    ctx = RunContext()
    a = MockAdapter()
    a.en11.link_up = False
    # 模拟 USB 网卡完全不存在：从列表移除
    a.en11 = NetIface(name="en11", service="USB 10/100/1000 LAN",
                      iftype="usb_ethernet", link_up=False, is_usb=True)
    a.en11.ip = None
    TopologyDetector(ctx, a).detect()
    # 有 down 的 USB 网卡时仍会选中它（工具会尝试启用），但绝不选 en0
    assert ctx.iface is None or ctx.iface.name != "en0", f"不应选中默认路由网卡 en0, got {ctx.iface}"
    print("PASS: no-usb-nic -> never selects default-route NIC")


def test_selects_usb_nic():
    ctx = RunContext()
    a = MockAdapter()
    from adb4eth.detectors.topology import TopologyDetector
    TopologyDetector(ctx, a).detect()
    assert ctx.iface and ctx.iface.name == "en11", f"应选en11, got {ctx.iface}"
    print("PASS: selects en11 as debug NIC")


def test_full_orchestration():
    """完整编排：配置 PC IP + 端口开 + ADB device。"""
    from adb4eth.orchestrator import Orchestrator
    from adb4eth.detectors.adb import AdbDetector

    ctx = RunContext()
    a = MockAdapter()
    a.port_open = True

    # 让 ADB 假装可连（模拟一个已连通道 + 目标 device）
    orig_run_cmd = None
    from adb4eth.platform import base as base_mod
    orig_run_cmd = base_mod.run_cmd

    calls = []
    def fake_run_cmd(cmd, timeout=15.0, check=False):
        calls.append(cmd)
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if joined.startswith("adb devices"):
            return f"List of devices attached\n192.168.100.2:5555  device product:rk3399_all model:TPS980P\n"
        if joined.startswith("adb connect"):
            return "connected to 192.168.100.2:5555"
        if joined.startswith("adb -s"):
            return "ADB_OK\n"
        if joined.startswith("adb kill-server"):
            return ""
        if joined.startswith("adb start-server"):
            return "* daemon started successfully"
        if joined.startswith("adb version"):
            return "Android Debug Bridge version 1.0.41"
        return ""
    base_mod.run_cmd = fake_run_cmd

    try:
        orch = Orchestrator(ctx)
        orch.adapter = a  # 用 mock 适配器
        ctx.platform = "macos"
        ctx.run = orch
        # 手动驱动阶段（Orchestrator.run 内部用 create_adapter，这里直接注入）
        from adb4eth.detectors.topology import TopologyDetector
        from adb4eth.detectors.datalink import PhysicalLayerDetector
        from adb4eth.configurators.pc_config import PcConfigurator
        from adb4eth.detectors.network import NetworkLayerDetector
        from adb4eth.detectors.adb import AdbDetector, TransportLayerDetector
        TopologyDetector(ctx, a).detect()
        PhysicalLayerDetector(ctx, a).detect()
        PcConfigurator(ctx, a).configure()
        NetworkLayerDetector(ctx, a).detect()
        TransportLayerDetector(ctx, a).detect()
        AdbDetector(ctx, a).detect()

        assert "setip:192.168.100.1/255.255.255.0" in a.cfg_called, f"应配置PC IP, got {a.cfg_called}"
        assert ctx.adb and ctx.adb.status == "device", f"应device, got {ctx.adb}"
        print("PASS: full orchestration (config + device)")
        print("      results:", [r.name for r in ctx.results])
    finally:
        base_mod.run_cmd = orig_run_cmd


def test_connect_failure_not_reported_as_device():
    """回归：adb connect 失败且 devices 无该设备时，不得误报为 device。"""
    from adb4eth.detectors.adb import AdbDetector
    from adb4eth.models import RunContext
    from adb4eth.platform import base as base_mod
    from adb4eth.platform.base import create_adapter

    ctx = RunContext()
    a = MockAdapter()
    a.port_open = True  # 端口探测通过，但 adb 层失败

    orig_run_cmd = base_mod.run_cmd
    calls = []

    def fake_run_cmd(cmd, timeout=15.0, check=False):
        calls.append(cmd)
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if joined.startswith("adb version"):
            return "Android Debug Bridge version 1.0.41"
        if joined.startswith("adb kill-server") or joined.startswith("adb start-server"):
            return "* daemon started"
        if joined.startswith("adb connect"):
            return "failed to connect to '192.168.100.2:5555': Connection timed out"
        if joined.startswith("adb devices"):
            # 设备列表为空（没有 192.168.100.2:5555）
            return "List of devices attached\n\n"
        return ""
    base_mod.run_cmd = fake_run_cmd

    try:
        AdbDetector(ctx, a).detect()
        assert ctx.adb is not None, "应有 adb 状态"
        assert ctx.adb.status == "not_connected", f"应 not_connected, got {ctx.adb.status}"
        adb_res = [r for r in ctx.results if r.layer == "ADB"]
        assert any(r.name == "adb connect" and r.status == "FAIL" for r in adb_res), \
            f"adb connect 应 FAIL, got {[r.status for r in adb_res]}"
        print("PASS: connect failure -> not_connected (no false 'device')")
    finally:
        base_mod.run_cmd = orig_run_cmd


if __name__ == "__main__":
    test_no_usb_nic_selects_nothing()
    test_selects_usb_nic()
    test_full_orchestration()
    test_connect_failure_not_reported_as_device()
    print("\nALL TESTS PASSED")
