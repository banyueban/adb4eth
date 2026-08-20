"""L4 传输层 + L7 应用层(ADB) 检测。"""

from __future__ import annotations

import time

from ..models import AdbState, DetResult, RunContext
from ..platform import base as _base
from ..platform.base import PlatformAdapter


class TransportLayerDetector:
    """L4: 收银机 ADB 端口监听探测。"""

    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def detect(self) -> list[DetResult]:
        results = []
        host = self.ctx.reg_ip
        port = self.ctx.adb_port
        ok = self.adapter.probe_port(host, port, timeout=3.0)
        results.append(
            DetResult(
                "L4",
                f"ADB端口 {port}",
                ok,
                "PASS" if ok else "FAIL",
                f"nc -z {host} {port} -> {'open' if ok else 'closed'}",
                "端口不通：收银机未开「网络调试」/ adbd未监听 / 以太网接口DOWN / 单向链路故障",
            )
        )
        self.ctx.results.extend(results)
        return results


class AdbDetector:
    """L7: adb connect 与状态解析。"""

    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def _adb(self, args: list[str], timeout: float = 15.0) -> str:
        # 通过模块属性动态取 run_cmd，便于测试/打包时替换
        return _base.run_cmd(["adb"] + args, timeout=timeout)

    def detect(self) -> list[DetResult]:
        results = []
        host = self.ctx.reg_ip
        port = self.ctx.adb_port

        # 先确认本机 adb 存在
        vout = _base.run_cmd(["adb", "version"], timeout=10)
        adb_exists = "Android Debug Bridge" in vout
        results.append(
            DetResult(
                "ADB",
                "adb工具",
                adb_exists,
                "PASS" if adb_exists else "FAIL",
                vout.strip().splitlines()[0] if vout else "",
                "未找到 adb：请安装 Android platform-tools",
            )
        )
        if not adb_exists:
            self.ctx.results.extend(results)
            return results

        self._adb(["kill-server"])
        self._adb(["start-server"])
        self._adb(["connect", f"{host}:{port}"], timeout=10)
        state = AdbState(host, port)

        # 以 `adb devices -l` 的真实设备状态为准做状态机恢复。
        # 场景：重插 USB 网卡后 adb server 常残留 offline；unauthorized 需等屏幕授权。
        attempts = 0
        while attempts < 5:
            status, model = self._device_state(host, port)
            if status == "device":
                break
            if status == "offline":
                # 主动断开 + 重启 server 清残留状态
                self._adb(["disconnect", f"{host}:{port}"], timeout=10)
                self._adb(["kill-server"])
                self._adb(["start-server"])
                time.sleep(1)
            elif status == "unauthorized":
                time.sleep(2)  # 等收银机屏幕授权
            else:
                # not_connected：链路/端口问题，交给下方统一判定，不重试
                break
            self._adb(["connect", f"{host}:{port}"], timeout=10)
            attempts += 1

        state.status, state.model = self._device_state(host, port)
        self.ctx.adb = state

        ok = state.status == "device"
        advice_map = {
            "offline": "ADB offline：在收银机重启adbd(或工具自动重启)，再重试",
            "unauthorized": "收银机屏幕弹窗请点「允许」，然后重新连接",
            "not_connected": "连接失败：回到L2/L4检查链路与端口",
        }
        results.append(
            DetResult(
                "ADB",
                "adb connect",
                ok,
                "PASS" if ok else "FAIL",
                f"adb connect {host}:{port} -> {state.status}"
                + (f" (model={state.model})" if state.model else ""),
                advice_map.get(state.status, "连接异常"),
            )
        )

        if ok:
            shell = self._adb(
                ["-s", f"{host}:{port}", "shell", "echo", "ADB_OK"], timeout=10
            )
            results.append(
                DetResult(
                    "ADB",
                    "shell可执行",
                    "ADB_OK" in shell,
                    "PASS" if "ADB_OK" in shell else "FAIL",
                    shell.strip()[:80],
                    "shell 执行失败：设备状态可能异常",
                )
            )
        self.ctx.results.extend(results)
        return results

    def _device_state(self, host: str, port: int) -> tuple:
        """从 `adb devices -l` 解析目标设备真实状态。设备不存在返回 not_connected。"""
        devices = self._adb(["devices", "-l"], timeout=10)
        for line in devices.splitlines():
            if line.startswith(f"{host}:{port}"):
                parts = line.split()
                status = parts[1] if len(parts) >= 2 else "unknown"
                model = None
                for p in parts[2:]:
                    if p.startswith("model:"):
                        model = p.split(":", 1)[1]
                return status, model
        return "not_connected", None
