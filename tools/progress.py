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
        """初始化事件历史与订阅者列表。"""
        self.events: list[ProgressEvent] = []
        self._listeners = list(listeners or [])

    def subscribe(self, listener: ProgressListener) -> None:
        """追加一个进度订阅者，例如 CLI 打印器或 Tkinter 渲染器。"""
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
        """创建、保存并广播一条事件；监听器失败不会影响业务流程。"""
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
        """发送阶段开始事件。"""
        return self.emit(stage, label, message, status="started", current=0 if total else None, total=total)

    def step(
        self, stage: str, label: str, message: str, *, current: int | None = None, total: int | None = None
    ) -> ProgressEvent:
        """发送阶段执行中的进度事件。"""
        return self.emit(stage, label, message, status="running", current=current, total=total)

    def completed(
        self, stage: str, label: str, message: str, *, total: int | None = None
    ) -> ProgressEvent:
        """发送阶段完成事件，并在有总量时把进度设为 100%。"""
        return self.emit(stage, label, message, status="completed", current=total, total=total)

    def failed(self, stage: str, label: str, message: str) -> ProgressEvent:
        """发送阶段失败事件；异常本身仍由业务层决定是否抛出。"""
        return self.emit(stage, label, message, status="failed")
