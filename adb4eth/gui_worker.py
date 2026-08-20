"""GUI 后台 worker：在独立线程中驱动核心流程，并把阶段进度/结果实时回传给界面。

通信方式：
- events 队列：UI 线程通过 after() 轮询，事件类型：
    ("stage", (phase, msg))   阶段进度消息
    ("iface_request", cands)  需要用户选择网卡（候选 NetIface 列表）
    ("summary", ctx)          整体流程结束，附最终 RunContext
    ("error", msg)            未捕获异常
- iface_responses 队列：UI 线程回传用户选择的网卡名（None 表示取消）。
- ctx.results 增量：UI 线程通过 latest_results(seen) 拉取新增的检测结果。
"""

from __future__ import annotations

import queue
import sys
import threading
from queue import Empty

from .models import NetIface, RunContext
from .orchestrator import Orchestrator


class GuiWorker:
    def __init__(self, platform: str | None = None, adapter=None):
        self.platform = platform
        self.adapter = adapter  # 测试注入用；None 时由 Orchestrator 自动创建
        self.events: queue.Queue[tuple] = queue.Queue()
        self.iface_responses: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ctx: RunContext | None = None
        self._cancel = threading.Event()
        self._rollback_lock = threading.Lock()
        self.full_mode = True  # 完整配置模式；仅检测时由 start(no_config=True) 置 False

    # ---------- UI 线程调用 ----------
    def start(
        self, no_config: bool, debug_net: str, pc_ip: str, reg_ip: str, adb_port: int
    ) -> bool:
        """启动后台线程。若已在运行返回 False。"""
        if self._thread and self._thread.is_alive():
            return False
        self._cancel.clear()
        platform = self.platform or (
            "windows" if sys.platform.startswith("win") else "macos"
        )
        self.full_mode = not no_config
        self._ctx = RunContext(
            platform=platform,
            debug_net=debug_net,
            pc_ip=pc_ip,
            reg_ip=reg_ip,
            adb_port=adb_port,
        )
        self._thread = threading.Thread(
            target=self._run, args=(no_config,), daemon=True, name="adb4eth-gui-worker"
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        """请求在阶段边界取消（尽力而为）。"""
        self._cancel.set()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def latest_results(self, seen: int) -> list:
        """返回 ctx.results[seen:]，供 UI 轮询增量刷新。"""
        if self._ctx is None:
            return []
        return list(self._ctx.results[seen:])

    def respond_iface(self, name: str | None) -> None:
        """回传用户选择的网卡名（None 表示取消选择）。"""
        self.iface_responses.put(name)

    def rollback(self) -> bool:
        """恢复配置快照（若有）。用于 GUI「回滚配置」按钮。"""
        with self._rollback_lock:
            ctx = self._ctx
            if ctx is None or not ctx.snapshot:
                return False
            try:
                from .platform.base import create_adapter

                adapter = self.adapter or create_adapter(ctx.platform)
                ok = True
                for name, snap in ctx.snapshot.iface_cfg.items():
                    # 从 ctx 里找回该接口
                    target = ctx.iface if ctx.iface and ctx.iface.name == name else None
                    if target and not adapter.rollback_iface(target, snap):
                        ok = False
                ctx.snapshot = None
                self.events.put(
                    ("stage", ("回滚", "已恢复配置快照" if ok else "部分回滚失败"))
                )
                return ok
            except Exception as e:
                self.events.put(("error", f"回滚失败: {e}"))
                return False

    # ---------- 后台线程 ----------
    def _select_iface(self, candidates: list[NetIface]) -> NetIface | None:
        """候选 >1 时向 UI 发起选择请求并等待响应；单候选直接自动选中。"""
        if len(candidates) == 1:
            return candidates[0]
        self.events.put(("iface_request", candidates))
        while True:
            if self._cancel.is_set():
                raise _Cancelled("已取消")
            try:
                name = self.iface_responses.get(timeout=0.2)
            except Empty:
                continue
            if name is None:
                return None
            for c in candidates:
                if c.name == name:
                    return c
            # 非法响应（理论上 UI 不会发生）：继续等待

    def _run(self, no_config: bool) -> None:
        ctx = self._ctx
        try:
            orch = Orchestrator(
                ctx, on_stage=self._on_stage, on_select_iface=self._select_iface
            )
            if self.adapter is not None:
                orch.adapter = self.adapter
            if no_config:
                orch.run_detect_only()
            else:
                orch.run()
        except _Cancelled:
            self.events.put(("summary", ctx))
            return
        except Exception as e:  # noqa: BLE001 —— UI 需兜底任何异常
            import traceback

            self.events.put(("error", f"{e}\n{traceback.format_exc()}"))
            return
        self.events.put(("summary", ctx))

    def _on_stage(self, phase: str, msg: str) -> None:
        if self._cancel.is_set():
            raise _Cancelled("已取消")
        self.events.put(("stage", (phase, msg)))


class _Cancelled(Exception):
    pass
