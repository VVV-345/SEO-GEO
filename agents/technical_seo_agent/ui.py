"""技术 SEO 审计 Agent 独立 Tkinter 界面。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from app import (
    create_run_context,
    render_technical_seo_report,
    run_technical_seo_audit,
    write_technical_seo_output,
)
from ui.app_state import AppState
from ui.task_runner import TaskRunner
from ui.widgets import FileSelector, ReadOnlyText


class TechnicalSEOAgentView(ttk.Frame):
    """独立运行的网站抓取、规则匹配和技术审计界面。"""

    title = "技术 SEO"

    def __init__(
        self,
        parent,
        *,
        state: AppState,
        task_runner: TaskRunner,
        set_status: Callable[[str], None],
    ) -> None:
        """保存共享依赖，初始化输入状态并创建控件。"""
        super().__init__(parent, padding=10)
        self.state = state
        self.task_runner = task_runner
        self.set_status = set_status
        self.domain_var = tk.StringVar(value="https://example.com")
        self.max_pages_var = tk.IntVar(value=30)
        self.lighthouse_var = tk.BooleanVar(value=True)
        self._build()
        self.task_runner.subscribe_busy(self._set_busy)

    def _build(self) -> None:
        """创建网站、业务上下文、抓取范围和报告区域。"""
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(self, text="网站审计输入", padding=10)
        right = ttk.LabelFrame(self, text="P0 / P1 / P2 审计报告", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(10, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(left, text="客户网站域名（必填）").grid(row=0, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.domain_var).grid(row=1, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="审计目标（可选）").grid(row=2, column=0, sticky="w")
        self.goal = tk.Text(left, height=3, wrap="word")
        self.goal.grid(row=3, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="业务背景（可选，用于解释优先级）").grid(row=4, column=0, sticky="w")
        self.business = tk.Text(left, height=4, wrap="word")
        self.business.grid(row=5, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="业务资料文件（可选）").grid(row=6, column=0, sticky="w")
        self.file_selector = FileSelector(left, height=3, title="选择技术审计业务资料")
        self.file_selector.grid(row=7, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="关键词 / 竞品上下文（可选，只用于优先级）").grid(row=8, column=0, sticky="w")
        self.search_context = tk.Text(left, height=3, wrap="word")
        self.search_context.grid(row=9, column=0, sticky="ew", pady=(3, 10))

        two_columns = ttk.Frame(left)
        two_columns.grid(row=10, column=0, sticky="nsew")
        two_columns.columnconfigure(0, weight=1)
        two_columns.columnconfigure(1, weight=1)
        two_columns.rowconfigure(1, weight=1)
        ttk.Label(two_columns, text="核心页面 URL（每行一个）").grid(row=0, column=0, sticky="w")
        ttk.Label(two_columns, text="不抓取路径前缀（每行一个）").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.core_urls = tk.Text(two_columns, height=7, wrap="none")
        self.core_urls.grid(row=1, column=0, sticky="nsew", pady=(3, 10))
        self.exclusions = tk.Text(two_columns, height=7, wrap="none")
        self.exclusions.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(3, 10))

        controls = ttk.Frame(left)
        controls.grid(row=12, column=0, sticky="ew")
        ttk.Checkbutton(
            controls, text="运行 Lighthouse（代表页）", variable=self.lighthouse_var
        ).pack(side="left")
        ttk.Label(controls, text="页面上限").pack(side="left", padx=(12, 4))
        ttk.Spinbox(controls, from_=1, to=500, width=6, textvariable=self.max_pages_var).pack(side="left")
        self.run_button = ttk.Button(controls, text="开始技术 SEO 审计", command=self._start)
        self.run_button.pack(side="right")
        ttk.Label(
            left,
            text="Mock 设置与关键词页同步。没有百度站长/GSC 数据时不判断实际收录、曝光或排名。",
            foreground="#666",
            wraplength=430,
        ).grid(row=13, column=0, sticky="w", pady=(10, 0))
        ttk.Label(
            right, text="工具事实 + 官方规则知识库 + LLM 整理 + Python 校验", foreground="#555"
        ).grid(row=0, column=0, sticky="w")
        self.output = ReadOnlyText(right)
        self.output.grid(row=1, column=0, sticky="nsew", pady=(4, 0))

    def _start(self) -> None:
        """校验域名、创建技术审计运行目录并启动任务。"""
        domain = self.domain_var.get().strip()
        if not domain:
            messagebox.showerror("输入错误", "请填写客户网站域名。")
            return
        core_urls = [line.strip() for line in self.core_urls.get("1.0", "end").splitlines() if line.strip()]
        exclusions = [line.strip() for line in self.exclusions.get("1.0", "end").splitlines() if line.strip()]
        project_name = domain.replace("https://", "").replace("http://", "").split("/", 1)[0]
        run = create_run_context([], project_name=project_name)
        self.state.technical_run = run
        args = {
            "domain": domain,
            "audit_goal": self.goal.get("1.0", "end").strip(),
            "business_text": self.business.get("1.0", "end").strip(),
            "material_files": self.file_selector.get_paths(),
            "search_context": self.search_context.get("1.0", "end").strip(),
            "core_urls": core_urls,
            "excluded_paths": exclusions,
            "max_pages": self.max_pages_var.get(),
            "run_lighthouse": self.lighthouse_var.get(),
            "mock": self.state.mock_mode,
        }
        self.output.set_text("正在发现 robots.txt、Sitemap 和站内页面…")

        def worker(progress):
            """执行抓取、规则、两阶段模型和输出保存。"""
            try:
                request, snapshot, output = run_technical_seo_audit(**args, progress=progress)
                paths = write_technical_seo_output(request, snapshot, output, run)
                return output, paths
            except Exception as error:
                run.update_run(status="failed", current_stage="technical_seo", error=str(error))
                raise

        self.task_runner.start(worker, on_success=self._finish, on_error=self._fail)

    def _finish(self, result) -> None:
        """显示技术审计报告与实际输出路径。"""
        output, (_report_json, report_md) = result
        self.output.set_text(render_technical_seo_report(output))
        self.set_status(f"技术审计完成：{len(output.findings)} 组问题；输出：{report_md.parent}")

    def _set_busy(self, busy: bool) -> None:
        """根据全局任务状态控制技术审计按钮。"""
        self.run_button.configure(state="disabled" if busy else "normal")

    def _fail(self, error: Exception) -> None:
        """显示技术审计失败原因。"""
        self.output.set_text("技术审计失败：\n" + str(error))
        self.set_status("技术审计失败")

