"""只负责组装各 Agent 独立 UI 的桌面主窗口。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from agents.content_brief_agent.ui import ContentBriefAgentView
from agents.keyword_agent.ui import KeywordAgentView
from agents.quality_agent.ui import QualityAgentView
from agents.serp_competitor_agent.ui import SerpCompetitorAgentView
from agents.technical_seo_agent.ui import TechnicalSEOAgentView
from agents.writing_agent.ui import WritingAgentView
from tools.progress import ProgressEvent

from .app_state import AppState
from .task_runner import TaskRunner
from .widgets import ReadOnlyText


AGENT_VIEWS = (
    KeywordAgentView,
    SerpCompetitorAgentView,
    TechnicalSEOAgentView,
    ContentBriefAgentView,
    WritingAgentView,
    QualityAgentView,
)


class SEOAgentUI(tk.Tk):
    """主窗口只管理标签页、全局状态、进度和共享依赖。"""

    def __init__(self) -> None:
        """初始化状态容器、任务器和各 Agent 标签页。"""
        super().__init__()
        self.title("SEO/GEO Agents")
        self.geometry("1320x900")
        self.minsize(1050, 720)
        self.state = AppState()
        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.IntVar(value=0)
        self.task_runner = TaskRunner(self, progress_listener=self._show_progress)
        self.agent_views: dict[str, ttk.Frame] = {}
        self._build()

    def _build(self) -> None:
        """创建主标题、Notebook、公共执行时间线和状态栏。"""
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        ttk.Label(
            root, text="SEO/GEO 多 Agent 工作台", font=("Microsoft YaHei UI", 17, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        tabs = ttk.Notebook(root)
        tabs.grid(row=1, column=0, sticky="nsew")
        for view_class in AGENT_VIEWS:
            view = view_class(
                tabs,
                state=self.state,
                task_runner=self.task_runner,
                set_status=self.set_status,
            )
            self.agent_views[view_class.title] = view
            tabs.add(view, text=view_class.title)

        timeline_frame = ttk.LabelFrame(root, text="执行步骤", padding=(8, 4))
        timeline_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        timeline_frame.columnconfigure(0, weight=1)
        self.timeline = ReadOnlyText(timeline_frame, height=5, foreground="#444")
        self.timeline.grid(row=0, column=0, sticky="ew")

        status = ttk.Frame(root)
        status.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Progressbar(status, maximum=100, variable=self.progress_var, length=280).grid(
            row=0, column=1, sticky="e"
        )

    def set_status(self, value: str) -> None:
        """更新主窗口公共状态文案。"""
        self.status_var.set(value)

    def _show_progress(self, event: ProgressEvent) -> None:
        """把任意 Agent 的通用进度事件显示在主窗口。"""
        suffix = f"（{event.current}/{event.total}）" if event.current is not None and event.total else ""
        self.status_var.set(f"{event.label}：{event.message}{suffix}")
        if event.percent is not None:
            self.progress_var.set(event.percent)
        elif event.status == "started":
            self.progress_var.set(0)
        self.timeline.append_text(f"[{event.status}] {event.label}：{event.message}{suffix}\n")


def launch() -> None:
    """启动组合后的多 Agent 桌面主窗口。"""
    SEOAgentUI().mainloop()

