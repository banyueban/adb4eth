"""平台命令适配层。

抽象平台差异：网卡枚举、默认路由、接口状态、静态 IP 配置（绝不设默认网关）、
ARP、端口探测等。Windows / macOS 分别实现。
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from subprocess import TimeoutExpired

from ..models import NetIface


class CommandError(RuntimeError):
    def __init__(self, cmd: str, rc: int, output: str):
        super().__init__(f"command failed (rc={rc}): {cmd}\n{output}")
        self.cmd = cmd
        self.rc = rc
        self.output = output


def _subprocess_kwargs() -> dict:
    """Windows 上抑制子进程弹出控制台黑框；其他平台无影响。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_cmd(
    cmd: list[str],
    timeout: float = 15.0,
    check: bool = False,
    encoding: str | None = None,
    errors: str = "replace",
) -> str:
    """执行命令，返回 stdout+stderr 合并文本。check=True 时失败抛 CommandError。

    encoding/errors：Windows 侧 PowerShell 输出统一按 UTF-8 解码，避免中文乱码。
    """
    kwargs = _subprocess_kwargs()
    if encoding is not None:
        kwargs["encoding"] = encoding
        kwargs["errors"] = errors
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )
    except TimeoutExpired:
        if check:
            raise CommandError(" ".join(cmd), -1, "TIMEOUT") from None
        return "TIMEOUT"
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise CommandError(" ".join(cmd), proc.returncode, out)
    return out


class PlatformAdapter(ABC):
    platform: str = "unknown"

    # ---------- 只读检测 ----------
    @abstractmethod
    def list_interfaces(self) -> list[NetIface]:
        """枚举物理网卡（过滤虚拟接口）。"""

    @abstractmethod
    def get_default_route_iface(self) -> str | None:
        """返回默认路由所在网卡名（如 en0），无则 None。"""

    @abstractmethod
    def refresh_iface(self, iface: NetIface) -> NetIface:
        """刷新单个接口的 IP/掩码/网关/链路/介质状态。"""

    @abstractmethod
    def get_arp_table(self) -> dict:
        """返回 {ip: mac}，仅本机直接可达的。"""

    @abstractmethod
    def probe_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """TCP 端口连通性探测。"""

    # ---------- 配置（写操作，需遵守默认路由保护） ----------
    @abstractmethod
    def enable_iface(self, iface: NetIface) -> bool:
        """启用网络服务/网卡。"""

    @abstractmethod
    def set_static_ip_no_gw(self, iface: NetIface, ip: str, mask: str) -> bool:
        """设置静态 IP/掩码，不设置默认网关。"""

    @abstractmethod
    def snapshot_iface(self, iface: NetIface) -> dict:
        """读取配置快照用于回滚。"""

    @abstractmethod
    def rollback_iface(self, iface: NetIface, snap: dict) -> bool:
        """恢复接口配置快照。"""

    @abstractmethod
    def ensure_priority(self, protect_iface: str, iface: NetIface) -> bool:
        """确保上网网卡优先于调试网卡（macOS 服务优先级；Windows 一般无需）。"""

    # ---------- 工具 ----------
    def is_admin(self) -> bool:
        """当前进程是否具备配置类操作所需权限。macOS 默认视为可写；Windows 需管理员。"""
        return True

    @staticmethod
    def ping(
        host: str, count: int = 3, timeout: float = 3.0, source: str | None = None
    ) -> bool:
        """ICMP 探测，可选绑定源 IP。跨平台（macOS/Windows）。

        以命令退出码判定结果，避免解析本地化输出（"0% loss" / "Lost = 0" /
        中文“丢失”等）。Windows: ping -n/-w/-S；macOS: ping -c/-t/-S。
        """
        try:
            wait_ms = int(timeout * 1000)
        except (TypeError, ValueError):
            wait_ms = 3000
        if os.name == "nt":
            cmd = ["ping", "-n", str(count), "-w", str(wait_ms)]
            if source:
                cmd += ["-S", source]
            cmd.append(host)
        elif source:
            cmd = ["ping", "-c", str(count), "-t", str(timeout), "-S", source, host]
        else:
            cmd = ["ping", "-c", str(count), "-t", str(timeout), host]
        try:
            run_cmd(cmd, timeout=timeout * count + 5, check=True)
            return True
        except CommandError:
            return False


def create_adapter(platform: str | None = None) -> PlatformAdapter:
    """根据平台创建适配器；platform 为空时自动探测。"""
    import sys

    if platform is None:
        platform = "windows" if sys.platform.startswith("win") else "macos"
    if platform == "macos":
        from .macos_adapter import MacOSAdapter

        return MacOSAdapter()
    elif platform == "windows":
        from .windows_adapter import WindowsAdapter

        return WindowsAdapter()
    raise ValueError(f"unsupported platform: {platform}")
