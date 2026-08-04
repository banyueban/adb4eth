# -*- coding: utf-8 -*-
"""报告器：将检测结果输出为控制台表格 + Markdown 报告。"""
from __future__ import annotations

import datetime
from typing import List

from .models import DetResult, RunContext


class Reporter:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    def console(self) -> str:
        lines = [f"adb4eth 有线ADB调试检测报告  [{self.ctx.platform}]"]
        lines.append("=" * 60)
        for r in self.ctx.results:
            mark = {"PASS": "[OK] ", "FAIL": "[XX] ", "WARN": "[!!] ", "SKIP": "[--] "}.get(r.status, "    ")
            lines.append(f"{mark}{r.layer:<6} {r.name:<20} {r.evidence}")
            if r.status in ("FAIL", "WARN") and r.advice:
                lines.append(f"        -> {r.advice}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# adb4eth 有线ADB调试检测报告",
            "",
            f"- 时间: {ts}",
            f"- 平台: {self.ctx.platform}",
            f"- 调试网卡: {self.ctx.iface.name if self.ctx.iface else 'N/A'}",
            f"- 默认路由网卡: {self.ctx.default_route_iface}",
            "",
            "## 检测结果",
            "",
            "| 层级 | 检查项 | 状态 | 结果 | 建议 |",
            "|---|---|---|---|---|",
        ]
        for r in self.ctx.results:
            evidence = (r.evidence or "").replace("|", "\\|").replace("\n", " ")[:80]
            advice = (r.advice or "").replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(f"| {r.layer} | {r.name} | {r.status} | {evidence} | {advice} |")
        if self.ctx.adb:
            a = self.ctx.adb
            lines.append("")
            lines.append("## ADB 连接")
            lines.append(f"- 目标: {a.host}:{a.port}")
            lines.append(f"- 状态: {a.status}")
            if a.model:
                lines.append(f"- 型号: {a.model}")
        lines.append("")
        lines.append("## 结论")
        fails = [r for r in self.ctx.results if r.status == "FAIL"]
        if fails:
            lines.append("存在 FAIL 项，请按上方建议处理后再试。")
        elif self.ctx.adb and self.ctx.adb.status == "device":
            lines.append("**有线 ADB 调试已就绪，可以直接使用。**")
        else:
            lines.append("检测完成，无 FAIL 项，但 ADB 尚未就绪。")
        return "\n".join(lines)

    def save(self, path: str) -> str:
        md = self.to_markdown()
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        return path
