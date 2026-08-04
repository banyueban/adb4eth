# -*- coding: utf-8 -*-
"""GUI 后台 worker：在独立线程中驱动核心流程，并把阶段进度/结果实时回传给界面。

通信方式：
- events 队列：UI 线程通过 after() 轮询，事件类型：
    ("stage", (phase, msg))   阶段进度消息
    ("summary", ctx)          整体流程结束，附最终 RunContext
    ("error", msg)            未捕获异常
- ctx.results 增量：UI 线程通过 latest_results(seen) 拉取新增的检测结果，
  实现"结果一产生就上屏"，无需为核心模块再增加回调。
"""
from __future__ import annotations

import queue
import sys
import threading
from typing import Optional

from .models import RunContext
from .orchestrator import Orchestrator


class GuiWorker:
    def __init__(self, platform: Optional[str] = None, adapter=None):
        self.platform = platform
        self.adapter = adapter  # 测试注入用；None 时由 Orchestrator 自动创建
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ctx: Optional[RunContext] = None
        self._cancel = threading.Event()
        self._rollback_lock = threading.Lock()

    # ---------- UI 线程调用 ----------
    def start(self, no_config: bool, debug_net: str, pc_ip: str, reg_ip: str,
              adb_port: int) -> bool:
        """启动后台线程。若已在运行返回 False。"""
        if self._thread and self._thread.is_alive():
            return False
        self._cancel.clear()
        platform = self.platform or ("windows" if sys.platform.startswith("win") else "macos")
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
                    if target:
                        if not adapter.rollback_iface(target, snap):
                            ok = False
                ctx.snapshot = None
                self.events.put(("stage", ("回滚", "已恢复配置快照" if ok else "部分回滚失败")))
                return ok
            except Exception as e:
                self.events.put(("error", f"回滚失败: {e}"))
                return False

    # ---------- 后台线程 ----------
    def _run(self, no_config: bool) -> None:
        ctx = self._ctx
        try:
            orch = Orchestrator(ctx, on_stage=self._on_stage)
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
