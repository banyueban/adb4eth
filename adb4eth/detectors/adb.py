# -*- coding: utf-8 -*-
"""L4 传输层 + L7 应用层(ADB) 检测。"""
from __future__ import annotations

from typing import List, Optional

from ..models import AdbState, DetResult, RunContext
from ..platform import base as _base
from ..platform.base import PlatformAdapter, run_shell


class TransportLayerDetector:
    """L4: 收银机 ADB 端口监听探测。"""

    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def detect(self) -> List[DetResult]:
        results = []
        host = self.ctx.reg_ip
        port = self.ctx.adb_port
        ok = self.adapter.probe_port(host, port, timeout=3.0)
        results.append(DetResult(
            "L4", f"ADB端口 {port}", ok, "PASS" if ok else "FAIL",
            f"nc -z {host} {port} -> {'open' if ok else 'closed'}",
            "端口不通：收银机未开「网络调试」/ adbd未监听 / 以太网接口DOWN / 单向链路故障",
        ))
        self.ctx.results.extend(results)
        return results


class AdbDetector:
    """L7: adb connect 与状态解析。"""

    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def _adb(self, args: List[str], timeout: float = 15.0) -> str:
        # 通过模块属性动态取 run_cmd，便于测试/打包时替换
        return _base.run_cmd(["adb"] + args, timeout=timeout)

    def detect(self) -> List[DetResult]:
        results = []
        host = self.ctx.reg_ip
        port = self.ctx.adb_port

        # 先确认本机 adb 存在
        vout = _base.run_cmd(["adb", "version"], timeout=10)
        adb_exists = "Android Debug Bridge" in vout
        results.append(DetResult(
            "ADB", "adb工具", adb_exists, "PASS" if adb_exists else "FAIL",
            vout.strip().splitlines()[0] if vout else "",
            "未找到 adb：请安装 Android platform-tools",
        ))
        if not adb_exists:
            self.ctx.results.extend(results)
            return results

        self._adb(["kill-server"])
        self._adb(["start-server"])
        out = self._adb(["connect", f"{host}:{port}"], timeout=10)
        state = self._parse(out)
        state.host = host
        state.port = port

        # unauthorized 时等收银机屏幕授权，最多重试几次
        retries = 0
        while self._parse(out).status == "unauthorized" and retries < 5:
            import time
            time.sleep(2)
            out = self._adb(["connect", f"{host}:{port}"], timeout=10)
            state = self._parse(out)
            state.host = host
            state.port = port
            retries += 1

        devices = self._adb(["devices", "-l"], timeout=10)
        dev_line = [l for l in devices.splitlines() if l.startswith(f"{host}:{port}")]
        status = "not_connected"
        model = None
        if dev_line:
            parts = dev_line[0].split()
            if len(parts) >= 2:
                status = parts[1]
            for p in parts:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
        state.status = status
        state.model = model
        self.ctx.adb = state

        ok = status == "device"
        advice_map = {
            "offline": "ADB offline：在收银机重启adbd(或工具自动重启)，再重试",
            "unauthorized": "收银机屏幕弹窗请点「允许」，然后重新连接",
            "not_connected": "连接失败：回到L2/L4检查链路与端口",
        }
        results.append(DetResult(
            "ADB", "adb connect", ok, "PASS" if ok else ("FAIL" if status != "device" else "WARN"),
            f"adb connect {host}:{port} -> {status}" + (f" (model={model})" if model else ""),
            advice_map.get(status, "连接异常"),
        ))

        if ok:
            shell = self._adb(["-s", f"{host}:{port}", "shell", "echo", "ADB_OK"], timeout=10)
            results.append(DetResult(
                "ADB", "shell可执行", "ADB_OK" in shell, "PASS" if "ADB_OK" in shell else "FAIL",
                shell.strip()[:80],
                "shell 执行失败：设备状态可能异常",
            ))
        self.ctx.results.extend(results)
        return results

    def _parse(self, out: str) -> AdbState:
        if "failed to connect" in out or "cannot connect" in out or "Connection" in out and "refused" in out:
            return AdbState("", status="not_connected")
        if "failed to authenticate" in out or "unauthorized" in out:
            return AdbState("", status="unauthorized")
        if "already connected" in out or "connected" in out:
            return AdbState("", status="device")
        return AdbState("", status="not_connected")
