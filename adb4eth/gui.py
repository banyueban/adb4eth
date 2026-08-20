"""adb4eth 图形界面（customtkinter）。

启动: python -m adb4eth --gui  （或 adb4eth-gui）
流程在后台线程(GuiWorker)中执行，UI 通过 after() 轮询事件队列与增量结果实时刷新。
"""

from __future__ import annotations

import contextlib
import ipaddress
import platform as _platform
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Literal

import customtkinter as ctk

from .gui_worker import GuiWorker

# ---------------------------------------------------------------- 设计令牌
if _platform.system() == "Windows":
    _APP_FONT = "Microsoft YaHei UI"
    _MONO_FONT = "Consolas"
else:
    _APP_FONT = "PingFang SC"
    _MONO_FONT = "SF Mono"

PAL = {
    "bg": "#0E1620",
    "panel": "#151F2B",
    "panel2": "#1B2735",
    "border": "#263447",
    "text": "#E7EEF6",
    "muted": "#8A98A9",
    "accent": "#3DDC84",  # Android 绿，作为品牌强调
    "ok": "#3DDC84",
    "warn": "#F5B84B",
    "fail": "#F0524C",
    "skip": "#6B7889",
    "accent_dark": "#0F3B2A",
}

STATUS_TEXT = {"PASS": "通过", "FAIL": "失败", "WARN": "警告", "SKIP": "跳过"}
STATUS_COLOR = {
    "PASS": PAL["ok"],
    "FAIL": PAL["fail"],
    "WARN": PAL["warn"],
    "SKIP": PAL["skip"],
}


def _font(size=13, weight: Literal["normal", "bold"] = "normal", mono=False):
    return ctk.CTkFont(
        family=_MONO_FONT if mono else _APP_FONT, size=size, weight=weight
    )


# ---------------------------------------------------------------- 小部件
def _labeled_entry(master, label, initial="", placeholder="", width=260):
    row = ctk.CTkFrame(master, fg_color="transparent")
    row.pack(fill="x", padx=14, pady=(10, 0))
    ctk.CTkLabel(
        row, text=label, font=_font(12), text_color=PAL["muted"], anchor="w"
    ).pack(fill="x")
    ent = ctk.CTkEntry(
        row,
        width=width,
        height=32,
        font=_font(13),
        fg_color=PAL["panel2"],
        border_color=PAL["border"],
        text_color=PAL["text"],
        corner_radius=8,
        placeholder_text=placeholder,
    )
    ent.pack(fill="x", pady=(4, 0))
    if initial:
        ent.insert(0, initial)
    return ent


# ---------------------------------------------------------------- 主窗口
class App(ctk.CTk):
    def __init__(self, platform: str | None = None):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.worker: GuiWorker = GuiWorker(platform=platform)
        self._seen = 0
        self._last_ctx = None
        self._has_results = False
        self._platform = platform or (
            "windows" if sys.platform.startswith("win") else "macos"
        )

        self.title("adb4eth — 有线 ADB 调试")
        self.geometry("1060x740")
        self.minsize(900, 620)
        self.configure(fg_color=PAL["bg"])
        self._build_ui()
        self._set_status("就绪", PAL["muted"])
        self.after(150, self._poll)

    # ---------------------------------------------------------- 布局
    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._header()
        self._left_panel()
        self._rail()
        self._footer()

    def _header(self):
        head = ctk.CTkFrame(self, fg_color=PAL["panel"], corner_radius=0, height=58)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        head.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(head, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w", padx=18, pady=8)
        ctk.CTkLabel(
            title_box,
            text="adb4eth",
            font=_font(19, "bold"),
            text_color=PAL["accent"],
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            title_box,
            text="有线 ADB 调试 · 自动检测与配置",
            font=_font(12),
            text_color=PAL["muted"],
            anchor="w",
        ).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            title_box,
            text=f"[{self._platform}]",
            font=_font(11, mono=True),
            text_color=PAL["skip"],
        ).pack(side="left", padx=(10, 0))

        self.status_pill = ctk.CTkLabel(
            head,
            text="● 就绪",
            font=_font(13, "bold"),
            text_color=PAL["muted"],
            fg_color=PAL["panel2"],
            corner_radius=14,
            height=28,
        )
        self.status_pill.grid(row=0, column=1, sticky="e", padx=18)

    def _left_panel(self):
        panel = ctk.CTkFrame(self, width=300, fg_color=PAL["panel"], corner_radius=0)
        panel.grid(row=1, column=0, sticky="nsw")
        panel.grid_propagate(False)

        ctk.CTkLabel(
            panel,
            text="调试参数",
            font=_font(12, "bold"),
            text_color=PAL["muted"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(16, 0))

        self.e_net = _labeled_entry(panel, "调试网段", "192.168.100", "如 192.168.100")
        self.e_pc = _labeled_entry(panel, "PC 端 IP", "192.168.100.1")
        self.e_reg = _labeled_entry(panel, "收银机 IP", "192.168.100.2")
        self.e_port = _labeled_entry(panel, "ADB 端口", "5555")

        btns = ctk.CTkFrame(panel, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(16, 0))
        self.btn_full = ctk.CTkButton(
            btns,
            text="开始检测并配置",
            font=_font(13, "bold"),
            fg_color=PAL["accent"],
            hover_color="#4CE89A",
            text_color="#06231A",
            height=38,
            corner_radius=10,
            command=lambda: self._start(full=True),
        )
        self.btn_full.pack(fill="x")
        self.btn_detect = ctk.CTkButton(
            btns,
            text="仅检测",
            font=_font(13),
            fg_color="transparent",
            border_width=1,
            border_color=PAL["border"],
            hover_color=PAL["panel2"],
            text_color=PAL["text"],
            height=34,
            corner_radius=10,
            command=lambda: self._start(full=False),
        )
        self.btn_detect.pack(fill="x", pady=(8, 0))
        self.btn_cancel = ctk.CTkButton(
            btns,
            text="停止",
            font=_font(13),
            fg_color="transparent",
            text_color=PAL["fail"],
            hover_color=PAL["panel2"],
            height=34,
            corner_radius=10,
            command=self._stop,
            state="disabled",
        )
        self.btn_cancel.pack(fill="x", pady=(8, 0))
        self.btn_rollback = ctk.CTkButton(
            btns,
            text="回滚配置",
            font=_font(13),
            fg_color="transparent",
            border_width=1,
            border_color=PAL["border"],
            hover_color=PAL["panel2"],
            text_color=PAL["warn"],
            height=34,
            corner_radius=10,
            command=self._rollback,
            state="disabled",
        )
        self.btn_rollback.pack(fill="x", pady=(8, 0))

        self.progress = ctk.CTkProgressBar(
            panel,
            height=6,
            corner_radius=3,
            mode="indeterminate",
            fg_color=PAL["panel2"],
            progress_color=PAL["accent"],
        )
        self.progress.pack(fill="x", padx=14, pady=(16, 0))

        ctk.CTkLabel(
            panel,
            text="运行日志",
            font=_font(12, "bold"),
            text_color=PAL["muted"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(16, 4))
        self.log = ctk.CTkTextbox(
            panel,
            fg_color=PAL["bg"],
            border_color=PAL["border"],
            border_width=1,
            font=_font(12, mono=True),
            text_color=PAL["muted"],
            corner_radius=8,
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 16))
        self.log.configure(state="disabled")

    def _rail(self):
        rail = ctk.CTkFrame(self, fg_color="transparent")
        rail.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(16, 8))
        rail.grid_rowconfigure(1, weight=1)
        rail.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            rail,
            text="逐层检测链路  L1 → L7",
            font=_font(14, "bold"),
            text_color=PAL["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        self.rail_scroll = ctk.CTkScrollableFrame(
            rail,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=PAL["border"],
            scrollbar_button_hover_color=PAL["skip"],
        )
        self.rail_scroll.grid(row=1, column=0, sticky="nsew")
        self.rail_scroll.grid_columnconfigure(0, weight=1)

        self.empty_hint = ctk.CTkLabel(
            self.rail_scroll,
            text="等待开始 — 点击左侧「开始检测并配置」\n"
            "工具将逐层检测 L1 物理 → L7 ADB，并在配置后自动连接。",
            font=_font(13),
            text_color=PAL["skip"],
            justify="center",
        )
        self.empty_hint.pack(pady=80)

    def _footer(self):
        foot = ctk.CTkFrame(self, fg_color=PAL["panel"], corner_radius=0, height=52)
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        foot.grid_columnconfigure(0, weight=1)

        self.foot_text = ctk.CTkLabel(
            foot,
            text="状态栏：准备就绪",
            font=_font(12),
            text_color=PAL["muted"],
            anchor="w",
        )
        self.foot_text.grid(row=0, column=0, sticky="w", padx=18)

        self.foot_adb = ctk.CTkLabel(
            foot,
            text="ADB：未连接",
            font=_font(12, mono=True),
            text_color=PAL["muted"],
            anchor="e",
        )
        self.foot_adb.grid(row=0, column=1, sticky="e", padx=18)

        self._build_menu()

    def _build_menu(self):
        with contextlib.suppress(Exception):  # 无菜单环境不影响功能
            mb = tk.Menu(self)
            m_file = tk.Menu(mb, tearoff=0)
            m_file.add_command(
                label="导出 Markdown 报告…",
                accelerator="Cmd/Ctrl+S",
                command=self._export,
            )
            m_file.add_separator()
            m_file.add_command(label="退出", command=self.destroy)
            mb.add_cascade(label="文件", menu=m_file)
            m_help = tk.Menu(mb, tearoff=0)
            m_help.add_command(label="关于 adb4eth", command=self._about)
            mb.add_cascade(label="帮助", menu=m_help)
            self.config(menu=mb)
            self.bind_all("<Command-s>", lambda e: self._export())
            self.bind_all("<Control-s>", lambda e: self._export())

    # ---------------------------------------------------------- 行为
    def _gather(self):
        """校验并返回参数；不合法时返回错误字符串。"""
        net = self.e_net.get().strip()
        pc = self.e_pc.get().strip()
        reg = self.e_reg.get().strip()
        port = self.e_port.get().strip()
        try:
            ipaddress.IPv4Address(pc)
            ipaddress.IPv4Address(reg)
            if not net or len(net.split(".")) != 3:
                raise ValueError
            port_num = int(port)
        except ValueError:
            return (
                None,
                None,
                None,
                None,
                "参数错误：请填写正确的网段（如 192.168.100）、PC/收银机 IP 与端口",
            )
        if not (pc.startswith(net + ".") and reg.startswith(net + ".")):
            return None, None, None, None, "参数错误：PC 与收银机 IP 需在调试网段内"
        return net, pc, reg, port_num, None

    def _start(self, full: bool):
        if self.worker.running():
            return
        net, pc, reg, port, err = self._gather()
        if err:
            self._set_status("参数错误", PAL["fail"])
            self._append_log(err)
            return
        assert (
            net is not None and pc is not None and reg is not None and port is not None
        )
        self._reset_rail()
        if not self.worker.start(
            no_config=not full, debug_net=net, pc_ip=pc, reg_ip=reg, adb_port=port
        ):
            return
        self._set_running(True)
        self._set_status("运行中", PAL["accent"])
        self._append_log(
            f"启动{'仅检测' if not full else '检测并配置'} → {pc} ↔ {reg}:{port}"
        )

    def _stop(self):
        if self.worker.running():
            self.worker.cancel()
            self._append_log("正在停止…")

    def _rollback(self):
        if self.worker.running():
            self._append_log("运行中，请先停止再回滚")
            return
        self._append_log("正在回滚配置快照…")
        if self.worker.rollback():
            self._append_log("已恢复配置快照")
            self.btn_rollback.configure(state="disabled")
        else:
            self._append_log("无可回滚的快照，或回滚失败")

    def _export(self):
        if not self._last_ctx:
            self._append_log("暂无结果可导出")
            return
        from .reporter import Reporter

        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            initialfile="adb4eth_report.md",
            filetypes=[("Markdown", "*.md"), ("文本", "*.txt")],
        )
        if not path:
            return
        try:
            saved = Reporter(self._last_ctx).save(path)
            self._append_log(f"报告已保存: {saved}")
        except Exception as e:
            self._append_log(f"导出失败: {e}")

    def _about(self):
        messagebox.showinfo(
            "关于 adb4eth",
            "adb4eth — 有线 ADB 调试自动检测与配置工具\n\n"
            "通过网线直连完成检测、安全配置与 adb connect。\n"
            "核心约束：绝不破坏 PC 默认路由（配置前快照、失败回滚）。",
        )

    # ---------------------------------------------------------- 状态
    def _set_running(self, on: bool):
        state = "disabled" if on else "normal"
        self.btn_full.configure(state=state)
        self.btn_detect.configure(state=state)
        self.btn_cancel.configure(state="normal" if on else "disabled")
        if on:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.set(0)

    def _set_status(self, text: str, color: str):
        self.status_pill.configure(text=f"● {text}", text_color=color)

    def _append_log(self, msg: str):
        import time

        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _reset_rail(self):
        for w in self.rail_scroll.winfo_children():
            w.destroy()
        self.empty_hint = ctk.CTkLabel(
            self.rail_scroll,
            text="检测中…",
            font=_font(13),
            text_color=PAL["skip"],
        )
        self.empty_hint.pack(pady=40)
        self._has_results = False
        self._seen = 0
        self.foot_adb.configure(text="ADB：未连接", text_color=PAL["muted"])

    def _add_result(self, r):
        color = STATUS_COLOR.get(r.status, PAL["skip"])
        card = ctk.CTkFrame(self.rail_scroll, fg_color=PAL["panel"], corner_radius=8)
        card.pack(fill="x", padx=(14, 6), pady=(0, 8))
        bar = ctk.CTkFrame(card, fg_color=color, width=4, height=40, corner_radius=0)
        bar.place(x=0, y=0, relheight=1)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=(14, 14), pady=9)

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top,
            text=r.layer,
            font=_font(11, mono=True),
            text_color=PAL["muted"],
            fg_color=PAL["panel2"],
            corner_radius=6,
            width=58,
            height=20,
        ).pack(side="left")
        ctk.CTkLabel(
            top,
            text=r.name,
            font=_font(13, "bold"),
            text_color=PAL["text"],
            anchor="w",
        ).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            top,
            text=STATUS_TEXT.get(r.status) or r.status,
            font=_font(11, "bold"),
            text_color=color,
            anchor="e",
        ).pack(side="right")

        if r.evidence:
            ctk.CTkLabel(
                body,
                text=r.evidence,
                font=_font(12, mono=True),
                text_color=PAL["muted"],
                anchor="w",
                justify="left",
                wraplength=560,
            ).pack(fill="x", pady=(5, 0))

        if r.advice and r.status in ("FAIL", "WARN"):
            ctk.CTkFrame(body, fg_color=PAL["panel2"], corner_radius=6).pack(
                fill="x", pady=(7, 0)
            )
            adv = ctk.CTkFrame(body, fg_color="transparent")
            adv.pack(fill="x", padx=10, pady=6)
            ctk.CTkLabel(
                adv,
                text="建议",
                font=_font(11, "bold"),
                text_color=color,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                adv,
                text=r.advice,
                font=_font(12),
                text_color=PAL["text"],
                anchor="w",
                justify="left",
                wraplength=520,
            ).pack(side="left", padx=(8, 0))

    # ---------------------------------------------------------- 网卡选择
    def _ask_iface(self, candidates) -> str | None:
        """模态弹窗选择网卡；返回选中网卡名或 None（取消）。"""
        top = ctk.CTkToplevel(self)
        top.title("选择调试网卡")
        top.geometry("680x480")
        top.transient(self)
        top.grab_set()
        top.configure(fg_color=PAL["bg"])

        result: dict[str, str | None] = {"name": None}

        def pick(name: str | None):
            result["name"] = name
            top.destroy()

        ctk.CTkLabel(
            top,
            text="发现多个有线网卡，请选择调试网卡：",
            font=_font(14, "bold"),
            text_color=PAL["text"],
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 8))

        scroll = ctk.CTkScrollableFrame(top, fg_color=PAL["panel"], corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        scroll.grid_columnconfigure(0, weight=1)

        ctx = self.worker._ctx
        for idx, c in enumerate(candidates, 1):
            row = ctk.CTkFrame(scroll, fg_color=PAL["panel2"], corner_radius=8)
            row.pack(fill="x", pady=4)
            row.grid_columnconfigure(0, weight=1)
            desc = f"[{idx}] {c.name}  ({c.vendor or c.iftype})"
            if c.is_usb:
                desc += "  USB"
            desc += "  up" if c.link_up else "  down"
            if c.ip:
                desc += f"  {c.ip}"
            if c.media:
                desc += f"  {c.media}"
            if ctx and ctx.default_route_iface and c.name == ctx.default_route_iface:
                desc += "  ⚠ 默认路由"
            ctk.CTkLabel(
                row,
                text=desc,
                font=_font(12, mono=True),
                text_color=PAL["text"],
                anchor="w",
                justify="left",
            ).grid(row=0, column=0, sticky="w", padx=10, pady=8)
            ctk.CTkButton(
                row,
                text="选择",
                width=64,
                height=28,
                font=_font(12),
                fg_color=PAL["accent"],
                text_color="#06231A",
                command=lambda name=c.name: pick(name),
            ).grid(row=0, column=1, padx=8)

        footer = ctk.CTkFrame(top, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(
            footer,
            text="取消",
            width=100,
            height=32,
            fg_color="transparent",
            border_width=1,
            border_color=PAL["border"],
            text_color=PAL["muted"],
            command=lambda: pick(None),
        ).pack(side="right")

        top.wait_window()
        name = result["name"]
        if name is None:
            return None
        if (
            self.worker.full_mode
            and ctx
            and ctx.default_route_iface
            and name == ctx.default_route_iface
            and not messagebox.askyesno(
                "确认选择默认路由网卡",
                f"「{name}」是当前默认路由网卡。\n配置后可能断开现有网络。\n确定继续吗？",
            )
        ):
            return None
        return name

    # ---------------------------------------------------------- 轮询
    def _poll(self):
        try:
            new = self.worker.latest_results(self._seen)
            if new:
                self._clear_empty_hint()
                for r in new:
                    self._add_result(r)
                self._seen += len(new)

            while True:
                try:
                    kind, payload = self.worker.events.get_nowait()
                except Exception:
                    break
                if kind == "stage":
                    phase, msg = payload
                    self._append_log(f"{phase}｜{msg}")
                elif kind == "iface_request":
                    chosen = self._ask_iface(payload)
                    self.worker.respond_iface(chosen)
                elif kind == "error":
                    self._set_running(False)
                    self._set_status("出错", PAL["fail"])
                    self._append_log(str(payload)[:500])
                    self.foot_text.configure(
                        text="运行出错，请查看日志", text_color=PAL["fail"]
                    )
                elif kind == "summary":
                    self._finish(payload)

            self.after(150, self._poll)
        except Exception as e:
            import traceback

            self._append_log(f"UI 内部错误: {e}\n{traceback.format_exc()}")
            self.after(150, self._poll)

    def _clear_empty_hint(self):
        if self.empty_hint is not None:
            with contextlib.suppress(Exception):
                self.empty_hint.destroy()
            self.empty_hint = None

    def _finish(self, ctx):
        # 补拉剩余结果，避免 summary 到达时结果尚未全部上屏
        rem = self.worker.latest_results(self._seen)
        if rem:
            self._clear_empty_hint()
            for r in rem:
                self._add_result(r)
            self._seen += len(rem)
        self._last_ctx = ctx
        self._set_running(False)
        # 有快照（曾修改过配置）时启用回滚按钮
        self.btn_rollback.configure(state="normal" if ctx.snapshot else "disabled")
        fails = [r for r in ctx.results if r.status == "FAIL"]
        adb = ctx.adb
        if adb and adb.status == "device":
            self._set_status("连接成功", PAL["ok"])
            self.foot_text.configure(
                text="有线 ADB 调试已就绪，可直接使用。", text_color=PAL["ok"]
            )
            self.foot_adb.configure(
                text=f"ADB：{adb.host}:{adb.port} · {adb.status}"
                + (f" · {adb.model}" if adb.model else ""),
                text_color=PAL["ok"],
            )
        elif fails:
            self._set_status("存在失败项", PAL["fail"])
            self.foot_text.configure(
                text=f"{len(fails)} 项失败，请按各卡片建议处理后重试。",
                text_color=PAL["fail"],
            )
            if adb:
                self.foot_adb.configure(
                    text=f"ADB：{adb.status}", text_color=PAL["fail"]
                )
        elif adb:
            self._set_status("未完全就绪", PAL["warn"])
            self.foot_text.configure(
                text="检测无失败项，但 ADB 尚未就绪。", text_color=PAL["warn"]
            )
            self.foot_adb.configure(text=f"ADB：{adb.status}", text_color=PAL["warn"])
        else:
            self._set_status("完成", PAL["muted"])
        self._append_log("流程结束。可通过 文件 → 导出 Markdown 报告。")


def main(platform: str | None = None) -> None:
    from .bundle import ensure_adb_on_path

    adb = ensure_adb_on_path()
    app = App(platform=platform)
    if adb:
        app._append_log(f"使用 adb: {adb}")
    app.mainloop()


if __name__ == "__main__":
    main()
