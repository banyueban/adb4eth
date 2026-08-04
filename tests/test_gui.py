# -*- coding: utf-8 -*-
"""GUI 冒烟测试：用 mock 适配器驱动 App 完整流程，验证 UI 状态切换与结果上屏。

无头环境（无 DISPLAY）下不运行 GUI 相关用例；有显示时构造真实 App。
"""
from __future__ import annotations

import sys
import time
import unittest

from adb4eth.platform import base as base_mod
from tests.test_smoke import MockAdapter


def _has_display() -> bool:
    if sys.platform.startswith("win"):
        return True
    if sys.platform == "darwin":
        return True  # macOS 有 WindowServer
    import os
    return bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(_has_display(), "无显示环境，跳过 GUI 用例")
class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        self._orig_run_cmd = base_mod.run_cmd

        def fake_run_cmd(cmd, timeout=15.0, check=False):
            joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if joined.startswith("adb devices"):
                return "List of devices attached\n192.168.100.2:5555  device product:rk3399_all model:TPS980P\n"
            if joined.startswith("adb connect"):
                return "connected to 192.168.100.2:5555"
            if joined.startswith("adb -s"):
                return "ADB_OK\n"
            if joined.startswith("adb kill-server") or joined.startswith("adb start-server"):
                return "* daemon started"
            if joined.startswith("adb version"):
                return "Android Debug Bridge version 1.0.41"
            return ""
        base_mod.run_cmd = fake_run_cmd

        self.mock = MockAdapter()
        self.mock.port_open = True

        import adb4eth.gui as g
        self._gui_mod = g
        self.app = g.App(platform="macos")
        # 用 mock 替换 worker 里的适配器
        from adb4eth.gui_worker import GuiWorker
        self.app.worker = GuiWorker(platform="macos", adapter=self.mock)

    def tearDown(self):
        base_mod.run_cmd = self._orig_run_cmd
        try:
            self.app.destroy()
        except Exception:
            pass

    def _wait_finish(self, timeout=12.0):
        """轮询 until worker 结束并处理完 summary 事件。
        注意：不能调用 app.update()——indeterminate 进度条会不断调度 after 回调，
        导致 update() 永不停歇。只用 update_idletasks() 处理重绘，手动 _poll() 处理队列。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.update_idletasks()
            self.app._poll()
            if self.app._last_ctx is not None:
                self.app.update_idletasks()
                return
            time.sleep(0.05)
        self.fail("GUI 流程未在超时内结束")

    def test_full_flow_reaches_device(self):
        self.app._start(full=True)
        self._wait_finish()
        ctx = self.app.worker._ctx
        self.assertIn("设置静态IP", [r.name for r in ctx.results])
        # 结果已上屏
        self.assertGreaterEqual(self.app._seen, 20, "应上屏至少 20 条结果")
        # 状态为连接成功
        self.assertEqual(self.app.status_pill.cget("text"), "● 连接成功")
        self.assertEqual(self.app.foot_adb.cget("text").startswith("ADB：192.168.100.2"), True)

    def test_param_validation(self):
        self.app.e_net.delete(0, "end"); self.app.e_net.insert(0, "bad")
        self.app._start(full=True)
        # 不合法参数不应启动 worker
        self.assertFalse(self.app.worker.running())
        self.assertIn("参数错误", self.app.log.get("1.0", "end"))

    def test_detect_only_mode(self):
        self.app._start(full=False)
        self._wait_finish()
        self.assertEqual(self.app.status_pill.cget("text"), "● 连接成功")


if __name__ == "__main__":
    unittest.main(verbosity=2)
