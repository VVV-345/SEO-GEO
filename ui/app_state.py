"""桌面 UI 的跨 Agent 状态与轻量事件总线。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


StateListener = Callable[[Any], None]


@dataclass
class AppState:
    """保存跨 Agent 共享结果；各 UI 模块不直接访问彼此控件。"""

    mock_mode: bool = True
    keyword_run: Any = None
    technical_run: Any = None
    candidate_output: Any = None
    serp_results: dict[str, Any] = field(default_factory=dict)
    keyword_output: Any = None
    competitor_output: Any = None
    _listeners: dict[str, list[StateListener]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )

    def subscribe(self, event: str, listener: StateListener) -> None:
        """订阅一个稳定事件名；同一监听器不会重复注册。"""
        if listener not in self._listeners[event]:
            self._listeners[event].append(listener)

    def unsubscribe(self, event: str, listener: StateListener) -> None:
        """移除事件监听器；未注册时保持幂等。"""
        if listener in self._listeners.get(event, []):
            self._listeners[event].remove(listener)

    def publish(self, event: str, payload: Any = None) -> None:
        """同步发布状态事件；监听器异常不会阻断其他模块。"""
        for listener in list(self._listeners.get(event, [])):
            try:
                listener(payload)
            except Exception:
                continue

    def set_mock_mode(self, enabled: bool) -> None:
        """更新全局 Mock 设置，并通知需要同步显示的 Agent UI。"""
        self.mock_mode = bool(enabled)
        self.publish("mock_mode_changed", self.mock_mode)

    def set_keyword_candidates(self, output: Any, run: Any) -> None:
        """保存候选输出和关键词运行目录，并清空上一轮 SERP 结果。"""
        self.candidate_output = output
        self.keyword_run = run
        self.keyword_output = None
        self.serp_results = {}
        self.publish("keyword_candidates_updated", output)
        self.publish("serp_results_updated", self.serp_results)

    def set_serp_results(self, results: dict[str, Any], output: Any) -> None:
        """保存关键词 SERP 和机会报告，供竞品 UI 自动刷新。"""
        self.serp_results = dict(results)
        self.keyword_output = output
        self.publish("serp_results_updated", self.serp_results)

    def set_competitor_output(self, output: Any) -> None:
        """保存最近一次竞品报告，供后续 Content Brief 等模块读取。"""
        self.competitor_output = output
        self.publish("competitor_output_updated", output)

