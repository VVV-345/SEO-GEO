"""跨 Agent 的结构化执行进度工具。

Agent 只负责发出事件，不依赖 CLI、Tkinter 或日志实现；调用方可订阅事件来渲染
终端输出、UI 时间线或持久化运行记录。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal


ProgressStatus = Literal["started", "running", "completed", "failed"]
ProgressListener = Callable[["ProgressEvent"], None]


@dataclass(frozen=True)
class ProgressEvent:
    """一次可显示、可记录的工作流状态变化。"""

    stage: str
    """稳定的机器标识，例如 ``keyword.serp``，供 UI 或日志筛选。"""

    label: str
    """面向用户的阶段名称，例如“查询百度 SERP”。"""

    message: str
    status: ProgressStatus = "running"
    current: int | None = None
    total: int | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def percent(self) -> int | None:
        """有总数时返回 0-100；未知总量的阶段由 UI 显示为不确定进度。"""
        if self.current is None or self.total is None or self.total <= 0:
            return None
        return max(0, min(100, round(self.current / self.total * 100)))


class ProgressReporter:
    """收集并广播进度事件，可被任意 Agent 共享。

    一个工作流传入同一个 reporter，事件就能形成跨 Agent 的完整时间线。
    监听器异常被隔离，避免 UI 渲染错误中断实际研究任务。
    """

    def __init__(self, listeners: list[ProgressListener] | None = None) -> None:
        self.events: list[ProgressEvent] = []
        self._listeners = list(listeners or [])

    def subscribe(self, listener: ProgressListener) -> None:
        self._listeners.append(listener)

    def emit(
        self,
        stage: str,
        label: str,
        message: str,
        *,
        status: ProgressStatus = "running",
        current: int | None = None,
        total: int | None = None,
    ) -> ProgressEvent:
        event = ProgressEvent(stage, label, message, status, current, total)
        self.events.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                # 进度展示是辅助能力，不能改变 Agent 的业务结果。
                continue
        return event

    def started(self, stage: str, label: str, message: str, *, total: int | None = None) -> ProgressEvent:
        return self.emit(stage, label, message, status="started", current=0 if total else None, total=total)

    def step(
        self, stage: str, label: str, message: str, *, current: int | None = None, total: int | None = None
    ) -> ProgressEvent:
        return self.emit(stage, label, message, status="running", current=current, total=total)

    def completed(
        self, stage: str, label: str, message: str, *, total: int | None = None
    ) -> ProgressEvent:
        return self.emit(stage, label, message, status="completed", current=total, total=total)

    def failed(self, stage: str, label: str, message: str) -> ProgressEvent:
        return self.emit(stage, label, message, status="failed")
