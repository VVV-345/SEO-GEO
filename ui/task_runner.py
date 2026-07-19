"""Tkinter 各 Agent UI 共用的后台任务与进度调度器。"""
from __future__ import annotations

import threading
from typing import Any, Callable

from tools.progress import ProgressEvent, ProgressReporter


TaskWorker = Callable[[ProgressReporter], Any]
TaskCallback = Callable[[Any], None]
ErrorCallback = Callable[[Exception], None]
BusyListener = Callable[[bool], None]


class TaskRunner:
    """在线程中执行耗时任务，并把所有 UI 回调安全切回主线程。"""

    def __init__(
        self,
        root,
        *,
        progress_listener: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        """保存 Tk 根对象和公共进度监听器。"""
        self.root = root
        self.progress_listener = progress_listener
        self._busy_listeners: list[BusyListener] = []
        self._active_tasks = 0

    def subscribe_busy(self, listener: BusyListener) -> None:
        """订阅全局忙碌状态，用于统一禁用可能冲突的操作。"""
        if listener not in self._busy_listeners:
            self._busy_listeners.append(listener)

    def _set_active_delta(self, delta: int) -> None:
        """更新活动任务数，并广播整体忙碌状态。"""
        self._active_tasks = max(0, self._active_tasks + delta)
        busy = self._active_tasks > 0
        for listener in list(self._busy_listeners):
            try:
                listener(busy)
            except Exception:
                continue

    def _queue_progress(self, event: ProgressEvent) -> None:
        """将后台进度事件调度到 Tk 主线程。"""
        if self.progress_listener is not None:
            self.root.after(0, lambda: self.progress_listener(event))

    def start(
        self,
        worker: TaskWorker,
        *,
        on_success: TaskCallback,
        on_error: ErrorCallback,
    ) -> None:
        """启动一个守护线程，并在结束后调用成功或错误回调。"""
        self._set_active_delta(1)

        def target() -> None:
            """执行实际任务，并将结果或异常送回主线程。"""
            try:
                reporter = ProgressReporter([self._queue_progress])
                result = worker(reporter)
            except Exception as error:
                self.root.after(0, lambda error=error: self._finish_error(error, on_error))
            else:
                self.root.after(0, lambda result=result: self._finish_success(result, on_success))

        threading.Thread(target=target, daemon=True).start()

    def _finish_success(self, result: Any, callback: TaskCallback) -> None:
        """先恢复忙碌状态，再把任务结果交给模块 UI。"""
        self._set_active_delta(-1)
        callback(result)

    def _finish_error(self, error: Exception, callback: ErrorCallback) -> None:
        """先恢复忙碌状态，再把异常交给模块 UI。"""
        self._set_active_delta(-1)
        callback(error)

