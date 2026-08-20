"""Windows 适配器单元测试：纯 mock，不要求 Windows 环境，可在 CI 三平台运行。"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from adb4eth.models import NetIface
from adb4eth.platform import base as base_mod
from adb4eth.platform import windows_adapter as wa_mod
from adb4eth.platform.base import CommandError, PlatformAdapter, ping_cmd
from adb4eth.platform.windows_adapter import WindowsAdapter


class WindowsAdapterTest(unittest.TestCase):
    def test_prefix_to_mask(self):
        self.assertEqual(WindowsAdapter._prefix_to_mask(0), "0.0.0.0")
        self.assertEqual(WindowsAdapter._prefix_to_mask(23), "255.255.254.0")
        self.assertEqual(WindowsAdapter._prefix_to_mask(24), "255.255.255.0")
        self.assertEqual(WindowsAdapter._prefix_to_mask(32), "255.255.255.255")

    def test_list_interfaces_distinguishes_usb_via_pnp(self):
        adapter = WindowsAdapter()
        adapters = [
            {
                "Name": "以太网",
                "InterfaceDescription": "Realtek PCIe GbE Family Controller",
                "Status": "Up",
                "LinkSpeed": "1 Gbps",
                "InterfaceIndex": 5,
                "PnPDeviceID": r"PCI\VEN_10EC&DEV_8168",
            },
            {
                "Name": "以太网 2",
                "InterfaceDescription": "ASIX AX88179 USB 3.0 to Gigabit Ethernet Adapter",
                "Status": "Up",
                "LinkSpeed": "1 Gbps",
                "InterfaceIndex": 6,
                "PnPDeviceID": r"USB\VID_0B95&PID_1790",
            },
        ]

        def fake_run_ps(script: str, timeout: float = 20.0, check: bool = False) -> str:
            if "Get-NetAdapter" in script:
                return json.dumps(adapters)
            if "Get-NetIPAddress" in script:
                return json.dumps({"IPAddress": "192.168.100.1", "PrefixLength": 24})
            return ""

        adapter._run_ps = fake_run_ps  # type: ignore[method-assign]
        ifaces = adapter.list_interfaces()
        self.assertEqual(len(ifaces), 2)
        self.assertEqual(ifaces[0].name, "以太网")
        self.assertEqual(ifaces[0].iftype, "ethernet")
        self.assertFalse(ifaces[0].is_usb)  # 内置 Realtek PCIe 不再误判为 USB
        self.assertEqual(ifaces[1].iftype, "usb_ethernet")
        self.assertTrue(ifaces[1].is_usb)
        self.assertEqual(ifaces[1].ip, "192.168.100.1")

    def test_list_interfaces_single_object_json(self):
        adapter = WindowsAdapter()
        adapters = {
            "Name": "以太网",
            "InterfaceDescription": "Realtek USB GbE Family Controller",
            "Status": "Up",
            "LinkSpeed": "1 Gbps",
            "InterfaceIndex": 7,
            "PnPDeviceID": r"USB\VID_0BDA&DEV_8156",
        }

        def fake_run_ps(script: str, timeout: float = 20.0, check: bool = False) -> str:
            if "Get-NetAdapter" in script:
                return json.dumps(adapters)
            return json.dumps({"IPAddress": None, "PrefixLength": None})

        adapter._run_ps = fake_run_ps  # type: ignore[method-assign]
        ifaces = adapter.list_interfaces()
        self.assertEqual(len(ifaces), 1)
        self.assertTrue(ifaces[0].is_usb)

    def test_set_static_ip_uses_netsh_and_raises_on_failure(self):
        adapter = WindowsAdapter()
        iface = NetIface(name="以太网", iftype="ethernet")
        calls = []

        def fake_run_cmd(
            cmd, timeout=15.0, check=False, encoding=None, errors="replace"
        ):
            calls.append(cmd)
            if check:
                raise CommandError(" ".join(cmd), 1, "denied")
            return ""

        with (
            mock.patch.object(wa_mod, "run_cmd", side_effect=fake_run_cmd),
            self.assertRaises(CommandError),
        ):
            adapter.set_static_ip_no_gw(iface, "192.168.100.1", "255.255.255.0")
        self.assertTrue(any("netsh" in c and "set" in c for c in calls))

    def test_is_admin(self):
        adapter = WindowsAdapter()
        adapter._run_ps = lambda script, timeout=20.0, check=False: "True"  # type: ignore[method-assign]
        self.assertTrue(adapter.is_admin())
        adapter._run_ps = lambda script, timeout=20.0, check=False: "False"  # type: ignore[method-assign]
        self.assertFalse(adapter.is_admin())

    def test_probe_port_uses_tcp_client(self):
        adapter = WindowsAdapter()
        scripts = []

        def fake(script: str, timeout: float = 20.0, check: bool = False) -> str:
            scripts.append(script)
            return "True"

        adapter._run_ps = fake  # type: ignore[method-assign]
        self.assertTrue(adapter.probe_port("192.168.100.2", 5555, timeout=2.0))
        self.assertIn("TcpClient", scripts[0])
        self.assertIn("WaitOne", scripts[0])
        self.assertIn("EndConnect", scripts[0])  # 先 EndConnect 再判 Connected，避免误报
        adapter._run_ps = lambda script, timeout=20.0, check=False: "False"  # type: ignore[method-assign]
        self.assertFalse(adapter.probe_port("192.168.100.2", 5555, timeout=2.0))

    def test_get_default_route_iface(self):
        adapter = WindowsAdapter()

        def fake(script: str, timeout: float = 20.0, check: bool = False) -> str:
            if "Get-NetRoute" in script:
                return json.dumps({"ifIndex": 12})
            if "Get-NetAdapter" in script:
                return "以太网"
            return ""

        adapter._run_ps = fake  # type: ignore[method-assign]
        self.assertEqual(adapter.get_default_route_iface(), "以太网")

    def test_snapshot_parses_addresses_and_gateway(self):
        adapter = WindowsAdapter()
        payload = json.dumps(
            {
                "ips": [
                    {"ip": "192.168.100.1", "prefix": 24, "dhcp": False},
                    {"ip": "192.168.100.10", "prefix": 24, "dhcp": False},
                ],
                "gw": "192.168.0.1",
                "metric": 55,
            }
        )
        adapter._run_ps = lambda script, timeout=20.0, check=False: payload  # type: ignore[method-assign]
        iface = NetIface(name="以太网")
        snap = adapter.snapshot_iface(iface)
        self.assertEqual(snap["ip"], "192.168.100.1")
        self.assertEqual(snap["mask"], "255.255.255.0")
        self.assertEqual(snap["gw"], "192.168.0.1")
        self.assertEqual(snap["metric"], 55)
        self.assertEqual(len(snap["addresses"]), 2)
        self.assertFalse(snap["dhcp"])

    def test_ensure_priority_sets_interface_metric(self):
        adapter = WindowsAdapter()
        scripts = []

        def fake(script: str, timeout: float = 20.0, check: bool = False) -> str:
            scripts.append(script)
            return ""

        adapter._run_ps = fake  # type: ignore[method-assign]
        iface = NetIface(name="以太网")
        self.assertTrue(adapter.ensure_priority("Wi-Fi", iface))
        self.assertIn("InterfaceMetric 1", scripts[0])

    def test_rollback_restores_metric(self):
        adapter = WindowsAdapter()
        iface = NetIface(name="以太网")
        snap = {
            "ip": "192.168.100.1",
            "mask": "255.255.255.0",
            "gw": None,
            "dhcp": True,
            "metric": 55,
            "addresses": [{"ip": "192.168.100.1", "prefix": 24, "dhcp": True}],
        }
        ps_scripts = []
        calls = []

        def fake_run_ps(script: str, timeout: float = 20.0, check: bool = False) -> str:
            ps_scripts.append(script)
            return ""

        def fake_run_cmd(
            cmd, timeout=15.0, check=False, encoding=None, errors="replace"
        ):
            calls.append(cmd)
            return ""

        adapter._run_ps = fake_run_ps  # type: ignore[method-assign]
        with mock.patch.object(wa_mod, "run_cmd", side_effect=fake_run_cmd):
            self.assertTrue(adapter.rollback_iface(iface, snap))
        self.assertTrue(any("dhcp" in c for c in calls))
        self.assertTrue(any("InterfaceMetric 55" in s for s in ps_scripts))

    def test_ping_uses_exit_code(self):
        with mock.patch.object(base_mod, "run_cmd", return_value=""):
            self.assertTrue(PlatformAdapter.ping("127.0.0.1", count=1, timeout=1))

        def fail(cmd, timeout=15.0, check=False, encoding=None, errors="replace"):
            raise CommandError(" ".join(cmd), 1, "unreachable")

        with mock.patch.object(base_mod, "run_cmd", side_effect=fail):
            self.assertFalse(PlatformAdapter.ping("192.0.2.1", count=1, timeout=1))

    def test_ping_cmd_returns_output(self):
        def fail(cmd, timeout=15.0, check=False, encoding=None, errors="replace"):
            raise CommandError(" ".join(cmd), 1, "Transmit failed. General failure.")

        with mock.patch.object(base_mod, "run_cmd", side_effect=fail):
            ok, out = ping_cmd("192.0.2.1", count=1, timeout=1)
        self.assertFalse(ok)
        self.assertIn("General failure", out)

    def test_run_cmd_missing_binary(self):
        with mock.patch.object(
            base_mod.subprocess, "run", side_effect=FileNotFoundError("no such file")
        ):
            self.assertIn("no such file", base_mod.run_cmd(["definitely-not-a-cmd"]))
        with (
            mock.patch.object(
                base_mod.subprocess,
                "run",
                side_effect=FileNotFoundError("no such file"),
            ),
            self.assertRaises(CommandError),
        ):
            base_mod.run_cmd(["definitely-not-a-cmd"], check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
