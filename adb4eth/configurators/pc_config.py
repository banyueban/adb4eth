# -*- coding: utf-8 -*-
"""PC 配置器：安全设置静态 IP，遵守默认路由保护，支持快照回滚。"""
from __future__ import annotations

from typing import List

from ..models import DetResult, RunContext, Snapshot
from ..platform.base import PlatformAdapter


class PcConfigurator:
    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def configure(self) -> List[DetResult]:
        results = []
        iface = self.ctx.iface
        if not iface:
            return results

        # 若已配好则跳过（幂等）
        fresh = self.adapter.refresh_iface(iface)
        self.ctx.iface = fresh
        if fresh.ip == self.ctx.pc_ip and self.ctx.pc_ip.startswith(self.ctx.debug_net):
            results.append(DetResult("CFG", "PC静态IP", True, "PASS",
                                     f"已配置 {fresh.ip}，跳过", ""))
            self.ctx.results.extend(results)
            return results

        # 快照
        snap = self.adapter.snapshot_iface(iface)
        self.ctx.snapshot = Snapshot(iface_cfg={iface.name: snap},
                                     default_route_iface=self.ctx.default_route_iface)

        try:
            # 1. 优先级保护（macOS）
            if self.ctx.default_route_iface:
                self.adapter.ensure_priority(self.ctx.default_route_iface, iface)
                results.append(DetResult("CFG", "服务优先级保护", True, "PASS",
                                         f"确保 {self.ctx.default_route_iface} 优先", ""))
            # 2. 启用接口
            self.adapter.enable_iface(iface)
            # 3. 设置静态 IP（不设网关）
            self.adapter.set_static_ip_no_gw(iface, self.ctx.pc_ip, self.ctx.mask)
            results.append(DetResult("CFG", "设置静态IP", True, "PASS",
                                     f"{iface.name}: {self.ctx.pc_ip}/{self.ctx.mask} (网关0.0.0.0,不产生默认路由)",
                                     ""))
        except Exception as e:
            results.append(DetResult("CFG", "设置静态IP", False, "FAIL", str(e),
                                     "配置失败：尝试恢复原配置"))
            self._rollback()
            self.ctx.results.extend(results)
            return results

        # 回验：默认路由保护 + 网段路由 + 链路
        try:
            def_if = self.adapter.get_default_route_iface()
            protected = (def_if == self.ctx.default_route_iface)
            results.append(DetResult("CFG", "回验-默认路由保护", protected,
                                     "PASS" if protected else "FAIL",
                                     f"默认路由: {def_if} (原: {self.ctx.default_route_iface})",
                                     "默认路由被改动，自动回滚"))
            if not protected:
                self._rollback()
                results.append(DetResult("CFG", "自动回滚", True, "WARN", "已回滚配置", ""))

            refreshed = self.adapter.refresh_iface(iface)
            self.ctx.iface = refreshed
            link_ok = refreshed.link_up
            results.append(DetResult("CFG", "回验-链路", link_ok,
                                     "PASS" if link_ok else "FAIL",
                                     f"{iface.name}: {'active' if link_ok else 'inactive'}",
                                     "链路异常：检查网线/扩展坞"))
            netroute = self._has_net_route(self.ctx.debug_net, iface.name)
            results.append(DetResult("CFG", "回验-网段路由", netroute,
                                     "PASS" if netroute else "WARN",
                                     f"{self.ctx.debug_net}.0/24 via {iface.name}",
                                     "未出现目标网段路由（可能仍需有效网关）"))
        except Exception as e:
            results.append(DetResult("CFG", "回验", False, "FAIL", str(e), "回验异常"))

        self.ctx.results.extend(results)
        return results

    def _has_net_route(self, net: str, iface_name: str) -> bool:
        from ..platform.base import run_shell
        out = run_shell("netstat -rn -f inet 2>/dev/null | grep -E 'default|%s'" % net)
        return net in out

    def _rollback(self):
        if not self.ctx.snapshot:
            return
        for name, snap in self.ctx.snapshot.iface_cfg.items():
            iface = self.ctx.iface if name == (self.ctx.iface.name if self.ctx.iface else "") else None
            if iface:
                self.adapter.rollback_iface(iface, snap)
