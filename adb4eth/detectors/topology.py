"""拓扑层检测：网卡枚举、默认路由识别、USB 网卡识别、候选网卡选择。

候选 = 所有物理有线网卡（ethernet + usb_ethernet），排除 Wi-Fi/虚拟/回环。
有 selector 且候选 >1 时交给用户选择；候选仅 1 个时自动选中；
无 selector（库调用/非交互）时使用安全自动策略：优先 USB 且非默认路由，
其次非默认路由有线网卡，绝不自动选默认路由网卡。
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import DetResult, NetIface, RunContext
from ..platform.base import PlatformAdapter


def build_candidates(
    ifaces: list[NetIface], default_route_iface: str | None
) -> list[NetIface]:
    """物理有线网卡候选，排除 Wi-Fi/虚拟/回环；排序：up 优先 → USB 优先 → 名称。"""
    cands = [i for i in ifaces if i.iftype in ("ethernet", "usb_ethernet")]
    cands.sort(key=lambda i: (not i.link_up, not i.is_usb, i.name.lower()))
    return cands


def auto_select(
    candidates: list[NetIface], default_route_iface: str | None
) -> NetIface | None:
    """安全自动策略：优先 USB 且非默认路由；其次非默认路由有线网卡；绝不自动选默认路由。"""
    safe = [c for c in candidates if c.name != default_route_iface]
    for c in safe:
        if c.is_usb:
            return c
    return safe[0] if safe else None


class TopologyDetector:
    def __init__(
        self,
        ctx: RunContext,
        adapter: PlatformAdapter,
        selector: Callable[[list[NetIface]], NetIface | None] | None = None,
    ):
        self.ctx = ctx
        self.adapter = adapter
        self.selector = selector

    def detect(self) -> list[DetResult]:
        results = []
        try:
            ifaces = self.adapter.list_interfaces()
        except Exception as e:
            results.append(
                DetResult(
                    "TOP",
                    "枚举网卡",
                    False,
                    "FAIL",
                    str(e),
                    "无法枚举网卡，请确认系统网络组件正常",
                )
            )
            self.ctx.results.extend(results)
            return results

        physical = [i for i in ifaces if i.is_physical]
        results.append(
            DetResult(
                "TOP",
                "物理网卡枚举",
                True,
                "PASS",
                "; ".join(
                    f"{i.name}({i.iftype},{'up' if i.link_up else 'down'}{',USB' if i.is_usb else ''})"
                    for i in physical
                )
                or "无",
                "未发现物理网卡：检查扩展坞/USB网卡是否插好",
            )
        )

        # 默认路由网卡
        try:
            def_if = self.adapter.get_default_route_iface()
        except Exception:
            def_if = None
        self.ctx.default_route_iface = def_if
        results.append(
            DetResult(
                "TOP",
                "默认路由网卡",
                bool(def_if),
                "PASS" if def_if else "WARN",
                f"{def_if}",
                "未识别到默认路由网卡，联网保护无法自动生效",
            )
        )

        # USB 网卡识别
        usb_nics = [i for i in physical if i.is_usb]
        results.append(
            DetResult(
                "TOP",
                "USB网卡识别",
                len(usb_nics) > 0,
                "PASS" if usb_nics else "WARN",
                "; ".join(f"{i.name}({i.vendor or 'unknown chip'})" for i in usb_nics)
                or "无",
                "未发现USB网卡：确认扩展坞/USB转网口已插入且被系统识别",
            )
        )

        # 候选列表
        candidates = build_candidates(physical, def_if)
        cand_desc = (
            "; ".join(
                f"{i.name}({i.iftype},{'up' if i.link_up else 'down'}"
                f"{',默认路由' if i.name == def_if else ''})"
                for i in candidates
            )
            or "无"
        )
        results.append(
            DetResult(
                "TOP",
                "候选网卡",
                len(candidates) > 0,
                "PASS" if candidates else "FAIL",
                cand_desc,
                "未找到可用有线调试网卡：请插入扩展坞/USB网卡或连接内置以太网",
            )
        )

        # 选择调试网卡
        selected = None
        if candidates:
            if self.selector is not None:
                # 显式 selector（CLI --iface / 交互 / GUI）始终调用：
                # 单候选快速路径由 selector 内部处理，--iface 校验也能生效
                try:
                    selected = self.selector(candidates)
                except Exception as e:
                    selected = None
                    results.append(
                        DetResult(
                            "TOP", "网卡选择", False, "FAIL", str(e), "选择网卡时出错"
                        )
                    )
            else:
                selected = auto_select(candidates, def_if)
        self.ctx.iface = selected

        if selected is None:
            msg = "未选择调试网卡" if candidates else "未发现可用有线网卡"
            results.append(
                DetResult(
                    "TOP",
                    "选定调试网卡",
                    False,
                    "FAIL",
                    msg,
                    "请重新运行并选择网卡，或插入有线网卡后重试",
                )
            )
        else:
            flag = "（默认路由网卡）" if selected.name == def_if else ""
            results.append(
                DetResult(
                    "TOP",
                    "选定调试网卡",
                    True,
                    "PASS",
                    f"{selected.name}({selected.vendor or 'unknown chip'}){flag}",
                    "",
                )
            )

        self.ctx.results.extend(results)
        return results
