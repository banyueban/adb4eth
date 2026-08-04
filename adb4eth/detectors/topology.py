# -*- coding: utf-8 -*-
"""拓扑层检测：网卡枚举、默认路由识别、USB 网卡识别、候选网卡选择。"""
from __future__ import annotations

from typing import List, Optional

from ..models import DetResult, NetIface, RunContext
from ..platform.base import PlatformAdapter


class TopologyDetector:
    def __init__(self, ctx: RunContext, adapter: PlatformAdapter):
        self.ctx = ctx
        self.adapter = adapter

    def detect(self) -> List[DetResult]:
        results = []
        try:
            ifaces = self.adapter.list_interfaces()
        except Exception as e:
            results.append(DetResult("TOP", "枚举网卡", False, "FAIL", str(e),
                                     "无法枚举网卡，请确认系统网络组件正常"))
            return results

        physical = [i for i in ifaces if i.is_physical]
        results.append(DetResult(
            "TOP", "物理网卡枚举", True, "PASS",
            "; ".join(f"{i.name}({i.iftype},{'up' if i.link_up else 'down'}{',USB' if i.is_usb else ''})"
                      for i in physical) or "无",
            "未发现物理网卡：检查扩展坞/USB网卡是否插好",
        ))

        # 默认路由网卡
        try:
            def_if = self.adapter.get_default_route_iface()
        except Exception:
            def_if = None
        self.ctx.default_route_iface = def_if
        results.append(DetResult(
            "TOP", "默认路由网卡", bool(def_if), "PASS" if def_if else "WARN",
            f"{def_if}",
            "未识别到默认路由网卡，联网保护无法自动生效",
        ))

        # USB 网卡识别
        usb_nics = [i for i in physical if i.is_usb]
        results.append(DetResult(
            "TOP", "USB网卡识别", len(usb_nics) > 0, "PASS" if usb_nics else "WARN",
            "; ".join(f"{i.name}({i.vendor or 'unknown chip'})" for i in usb_nics) or "无",
            "未发现USB网卡：确认扩展坞/USB转网口已插入且被系统识别",
        ))

        # 选择调试网卡：只选 USB 网卡（扩展坞），绝不动内置网卡/默认路由网卡
        # 理由：内置有线网卡可能是用户正常联网通道，误配会破坏原网络
        candidate = None
        for i in usb_nics:
            if i.name == self.ctx.default_route_iface:
                continue  # 默认路由网卡绝不配置
            if candidate is None:
                candidate = i
            elif i.link_up and not candidate.link_up:
                candidate = i  # 优先已 up 的 USB 网卡
        self.ctx.iface = candidate
        selected_msg = f"{candidate.name}({candidate.vendor or 'unknown chip'})" if candidate else "无"
        if candidate is None and not usb_nics:
            selected_msg = "未发现USB网卡"
        results.append(DetResult(
            "TOP", "选定调试网卡", candidate is not None, "PASS" if candidate else "FAIL",
            selected_msg,
            "未插入 USB 扩展坞网卡：请插入扩展坞/USB网卡后重试（为避免破坏联网，不会修改内置网卡）",
        ))

        self.ctx.results.extend(results)
        return results
