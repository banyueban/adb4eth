"""自测脚本：用模拟的 USB 网卡环境验证完整编排流程（不修改真实网络）。"""

from __future__ import annotations

import io
import os
import sys
import threading
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adb4eth.cli import interactive_select, make_selector, resolve_iface_arg
from adb4eth.configurators.pc_config import PcConfigurator
from adb4eth.detectors.adb import AdbDetector, TransportLayerDetector
from adb4eth.detectors.datalink import PhysicalLayerDetector
from adb4eth.detectors.network import NetworkLayerDetector
from adb4eth.detectors.topology import TopologyDetector
from adb4eth.gui_worker import GuiWorker
from adb4eth.models import NetIface, RunContext
from adb4eth.orchestrator import Orchestrator
from adb4eth.platform import base as base_mod
from adb4eth.platform.base import PlatformAdapter


class MockAdapter(PlatformAdapter):
    """模拟一个带 USB 网卡的 macOS 环境。"""

    def __init__(self):
        self.en11 = NetIface(
            name="en11",
            service="USB 10/100/1000 LAN",
            iftype="usb_ethernet",
            ip="169.254.118.5",
            mask="255.255.0.0",
            link_up=True,
            media="100baseTX <full-duplex>",
            vendor="RTL8153",
            is_usb=True,
        )
        self.en0 = NetIface(
            name="en0",
            service="Wi-Fi",
            iftype="wifi",
            ip="192.168.0.151",
            mask="255.255.255.0",
            link_up=True,
            media="autoselect",
        )
        self.default_route = "en0"
        self._ifaces: list[NetIface] | None = None
        self.cfg_called = []
        self.port_open = False

    def add_iface(self, iface: NetIface) -> None:
        if self._ifaces is None:
            self._ifaces = [self.en0, self.en11]
        self._ifaces.append(iface)

    def list_interfaces(self):
        return list(self._ifaces) if self._ifaces is not None else [self.en0, self.en11]

    def get_default_route_iface(self):
        return self.default_route

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
        return {
            "ip": "169.254.118.5",
            "mask": "255.255.0.0",
            "gw": None,
            "enabled": True,
            "svc": iface.service,
        }

    def rollback_iface(self, iface, snap):
        self.cfg_called.append("rollback")
        return True

    def ensure_priority(self, protect_iface, iface):
        self.cfg_called.append("priority")
        return True


def test_no_usb_nic_selects_nothing():
    """无 USB 网卡时不选默认路由网卡作为调试网卡。"""
    ctx = RunContext()
    a = MockAdapter()
    a.en11.link_up = False
    # 模拟 USB 网卡完全不存在：从列表移除
    a.en11 = NetIface(
        name="en11",
        service="USB 10/100/1000 LAN",
        iftype="usb_ethernet",
        link_up=False,
        is_usb=True,
    )
    a.en11.ip = None
    TopologyDetector(ctx, a).detect()
    # 有 down 的 USB 网卡时仍会选中它（工具会尝试启用），但绝不选 en0
    assert ctx.iface is None or ctx.iface.name != "en0", (
        f"不应选中默认路由网卡 en0, got {ctx.iface}"
    )
    print("PASS: no-usb-nic -> never selects default-route NIC")


def test_selects_usb_nic():
    ctx = RunContext()
    a = MockAdapter()
    TopologyDetector(ctx, a).detect()
    assert ctx.iface and ctx.iface.name == "en11", f"应选en11, got {ctx.iface}"
    print("PASS: selects en11 as debug NIC")


def test_full_orchestration():
    """完整编排：配置 PC IP + 端口开 + ADB device。"""
    ctx = RunContext()
    a = MockAdapter()
    a.port_open = True

    orig_run_cmd = base_mod.run_cmd
    calls = []

    def fake_run_cmd(cmd, timeout=15.0, check=False):
        calls.append(cmd)
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if joined.startswith("adb devices"):
            return "List of devices attached\n192.168.100.2:5555  device product:rk3399_all model:TPS980P\n"
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
        # 手动驱动阶段（Orchestrator.run 内部用 create_adapter，这里直接注入）
        TopologyDetector(ctx, a).detect()
        PhysicalLayerDetector(ctx, a).detect()
        PcConfigurator(ctx, a).configure()
        NetworkLayerDetector(ctx, a).detect()
        TransportLayerDetector(ctx, a).detect()
        AdbDetector(ctx, a).detect()

        assert "setip:192.168.100.1/255.255.255.0" in a.cfg_called, (
            f"应配置PC IP, got {a.cfg_called}"
        )
        assert ctx.adb and ctx.adb.status == "device", f"应device, got {ctx.adb}"
        print("PASS: full orchestration (config + device)")
        print("      results:", [r.name for r in ctx.results])
    finally:
        base_mod.run_cmd = orig_run_cmd


def test_connect_failure_not_reported_as_device():
    """回归：adb connect 失败且 devices 无该设备时，不得误报为 device。"""
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
        if joined.startswith(("adb kill-server", "adb start-server")):
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
        assert ctx.adb.status == "not_connected", (
            f"应 not_connected, got {ctx.adb.status}"
        )
        adb_res = [r for r in ctx.results if r.layer == "ADB"]
        assert any(r.name == "adb connect" and r.status == "FAIL" for r in adb_res), (
            f"adb connect 应 FAIL, got {[r.status for r in adb_res]}"
        )
        print("PASS: connect failure -> not_connected (no false 'device')")
    finally:
        base_mod.run_cmd = orig_run_cmd


def test_offline_recovered_by_restart():
    """回归：offline 状态通过 disconnect + kill/start-server 重试恢复为 device。"""
    ctx = RunContext()
    a = MockAdapter()
    a.port_open = True

    orig_run_cmd = base_mod.run_cmd
    connect_count = {"n": 0}

    def fake_run_cmd(cmd, timeout=15.0, check=False):
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if joined.startswith("adb version"):
            return "Android Debug Bridge version 1.0.41"
        if joined.startswith(("adb kill-server", "adb start-server")):
            return "* daemon restarted"
        if joined.startswith("adb disconnect"):
            return ""
        if joined.startswith("adb connect"):
            connect_count["n"] += 1
            # 第一次 connect 后设备 offline，重试后恢复 device
            return "connected to 192.168.100.2:5555"
        if joined.startswith("adb devices"):
            n = connect_count["n"]
            if n <= 1:
                return "List of devices attached\n192.168.100.2:5555 offline\n"
            return "List of devices attached\n192.168.100.2:5555  device product:rk3399_all model:TPS980P\n"
        return ""

    base_mod.run_cmd = fake_run_cmd
    try:
        AdbDetector(ctx, a).detect()
        assert ctx.adb is not None
        assert ctx.adb.status == "device", (
            f"offline 重试后应 device, got {ctx.adb.status}"
        )
        adb_res = [r for r in ctx.results if r.layer == "ADB"]
        assert any(r.name == "adb connect" and r.status == "PASS" for r in adb_res), (
            f"adb connect 应 PASS, got {[r.status for r in adb_res]}"
        )
        print("PASS: offline recovered via disconnect + server restart")
    finally:
        base_mod.run_cmd = orig_run_cmd


class TopologySelectionTest(unittest.TestCase):
    """候选网卡与用户选择回调。"""

    def test_selector_receives_candidates_and_chooses(self):
        ctx = RunContext()
        a = MockAdapter()
        a.add_iface(
            NetIface(
                name="en12",
                service="USB LAN 2",
                iftype="usb_ethernet",
                link_up=False,
                is_usb=True,
            )
        )
        seen = []

        def selector(cands):
            seen.append(cands)
            return next(c for c in cands if c.name == "en12")

        TopologyDetector(ctx, a, selector=selector).detect()
        assert ctx.iface is not None
        self.assertEqual(ctx.iface.name, "en12")
        self.assertGreaterEqual(len(seen), 1)

    def test_auto_prefers_usb_and_skips_default_route(self):
        ctx = RunContext()
        a = MockAdapter()
        a.add_iface(NetIface(name="en5", iftype="ethernet", link_up=True, is_usb=False))
        TopologyDetector(ctx, a).detect()
        assert ctx.iface is not None
        self.assertEqual(ctx.iface.name, "en11")

    def test_auto_never_selects_default_route(self):
        ctx = RunContext()
        a = MockAdapter()
        a.default_route = "en5"
        en5 = NetIface(name="en5", iftype="ethernet", link_up=True, is_usb=False)
        a._ifaces = [a.en0, en5]  # 唯一有线候选就是默认路由网卡
        TopologyDetector(ctx, a).detect()
        self.assertIsNone(ctx.iface)  # 绝不自动选默认路由网卡


class CliSelectorTest(unittest.TestCase):
    """CLI 网卡选择逻辑。"""

    def test_resolve_iface_arg(self):
        cands = [NetIface(name="en5"), NetIface(name="en11")]
        chosen = resolve_iface_arg(cands, "en11")
        assert chosen is not None
        self.assertEqual(chosen.name, "en11")
        self.assertIsNone(resolve_iface_arg(cands, "wlan0"))

    def test_make_selector_iface_arg_single_candidate(self):
        ctx = RunContext()
        cands = [NetIface(name="en11")]
        sel = make_selector(ctx, full_mode=True, iface_arg="nope")
        assert sel is not None
        self.assertIsNone(sel(cands))  # 单候选也要校验 --iface
        sel = make_selector(ctx, full_mode=True, iface_arg="en11")
        assert sel is not None
        chosen = sel(cands)
        assert chosen is not None
        self.assertEqual(chosen.name, "en11")

    def test_interactive_select_invalid_then_valid(self):
        ctx = RunContext()
        cands = [
            NetIface(name="en5", iftype="ethernet"),
            NetIface(name="en11", iftype="usb_ethernet"),
        ]
        stdin = io.StringIO("abc\n2\n")
        chosen = interactive_select(
            cands, ctx, full_mode=False, stdin=stdin, stdout=io.StringIO()
        )
        assert chosen is not None
        self.assertEqual(chosen.name, "en11")

    def test_interactive_select_default_route_requires_confirm(self):
        ctx = RunContext()
        ctx.default_route_iface = "en5"
        cands = [
            NetIface(name="en5", iftype="ethernet"),
            NetIface(name="en11", iftype="usb_ethernet"),
        ]
        stdin = io.StringIO("1\nn\n")
        chosen = interactive_select(
            cands, ctx, full_mode=True, stdin=stdin, stdout=io.StringIO()
        )
        self.assertIsNone(chosen)
        stdin = io.StringIO("1\ny\n")
        chosen = interactive_select(
            cands, ctx, full_mode=True, stdin=stdin, stdout=io.StringIO()
        )
        assert chosen is not None
        self.assertEqual(chosen.name, "en5")


class GuiWorkerSelectionTest(unittest.TestCase):
    """GUI worker 网卡选择队列（无需显示器）。"""

    def test_worker_select_iface_waits_and_responds(self):
        w = GuiWorker(platform="macos")
        w._ctx = RunContext()
        cands = [NetIface(name="en5"), NetIface(name="en11")]
        result = {}

        def target():
            result["iface"] = w._select_iface(cands)

        t = threading.Thread(target=target)
        t.start()
        kind, payload = w.events.get(timeout=3)
        self.assertEqual(kind, "iface_request")
        self.assertEqual(payload, cands)
        w.respond_iface("en11")
        t.join(timeout=3)
        self.assertFalse(t.is_alive())
        iface = result["iface"]
        assert iface is not None
        self.assertEqual(iface.name, "en11")

    def test_worker_auto_selects_single_candidate(self):
        w = GuiWorker()
        w._ctx = RunContext()
        c = NetIface(name="en11")
        self.assertIs(w._select_iface([c]), c)
        self.assertTrue(w.events.empty())


if __name__ == "__main__":
    test_no_usb_nic_selects_nothing()
    test_selects_usb_nic()
    test_full_orchestration()
    test_connect_failure_not_reported_as_device()
    test_offline_recovered_by_restart()
    print("\nALL TESTS PASSED")
