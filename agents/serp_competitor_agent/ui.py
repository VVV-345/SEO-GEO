"""SERP + 竞品分析 Agent 独立 Tkinter 界面。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from app import analyze_serp_competitors, render_competitor_report, write_competitor_output
from ui.app_state import AppState
from ui.task_runner import TaskRunner
from ui.widgets import ReadOnlyText


class SerpCompetitorAgentView(ttk.Frame):
    """接收共享 SERP 结果，分析一个确定关键词的竞品页面。"""

    title = "SERP + 竞品"

    def __init__(
        self,
        parent,
        *,
        state: AppState,
        task_runner: TaskRunner,
        set_status: Callable[[str], None],
    ) -> None:
        """保存共享状态与任务器，并创建竞品分析界面。"""
        super().__init__(parent, padding=10)
        self.state = state
        self.task_runner = task_runner
        self.set_status = set_status
        self.keyword_var = tk.StringVar()
        self.limit_var = tk.IntVar(value=10)
        self._build()
        self.state.subscribe("serp_results_updated", self._refresh_choices)
        self.task_runner.subscribe_busy(self._set_busy)

    def _build(self) -> None:
        """创建关键词选择、URL 编辑、运行控制和报告区域。"""
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(self, text="分析输入", padding=10)
        right = ttk.LabelFrame(self, text="竞品分析输出", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(left, text="确定关键词（每次只分析一个）").grid(row=0, column=0, sticky="w")
        self.keyword_box = ttk.Combobox(left, textvariable=self.keyword_var)
        self.keyword_box.grid(row=1, column=0, sticky="ew", pady=(3, 10))
        self.keyword_box.bind("<<ComboboxSelected>>", self._load_urls)
        ttk.Label(left, text="SERP 落地页 URL（每行一个，可增删；最多 10 个）").grid(
            row=2, column=0, sticky="w"
        )
        self.urls = tk.Text(left, wrap="none")
        self.urls.grid(row=3, column=0, sticky="nsew", pady=(3, 10))
        controls = ttk.Frame(left)
        controls.grid(row=4, column=0, sticky="ew")
        ttk.Label(controls, text="抓取页数").pack(side="left")
        ttk.Spinbox(controls, from_=1, to=10, width=5, textvariable=self.limit_var).pack(
            side="left", padx=(5, 0)
        )
        self.run_button = ttk.Button(
            controls, text="开始 SERP + 竞品分析", command=self._start, state="disabled"
        )
        self.run_button.pack(side="right")
        ttk.Label(
            left,
            text="关键词和 URL 来自 AppState；也可人工修改。Mock 设置与关键词页同步。",
            foreground="#666",
            wraplength=420,
        ).grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Label(right, text="共同主题、FAQ、内容缺口、必写项和建议结构", foreground="#555").grid(
            row=0, column=0, sticky="w"
        )
        self.output = ReadOnlyText(right)
        self.output.grid(row=1, column=0, sticky="nsew", pady=(4, 0))

    def _refresh_choices(self, _payload=None) -> None:
        """根据 AppState 中有 URL 的关键词刷新下拉框。"""
        keywords = [
            keyword for keyword, result in self.state.serp_results.items() if result.results
        ]
        self.keyword_box.configure(values=keywords)
        if keywords and self.keyword_var.get() not in keywords:
            self.keyword_var.set(keywords[0])
            self._load_urls()
        self.run_button.configure(
            state="normal" if self.state.keyword_run is not None else "disabled"
        )

    def _load_urls(self, _event=None) -> None:
        """把当前关键词的真实 SERP 落地页写入可编辑文本框。"""
        result = self.state.serp_results.get(self.keyword_var.get())
        if result is None:
            return
        self.urls.delete("1.0", "end")
        self.urls.insert("1.0", "\n".join(item.url for item in result.results))

    def _start(self) -> None:
        """校验单关键词、URL 和运行上下文后启动竞品任务。"""
        keyword = self.keyword_var.get().strip()
        urls = list(dict.fromkeys(
            line.strip() for line in self.urls.get("1.0", "end").splitlines() if line.strip()
        ))
        run = self.state.keyword_run
        if not keyword:
            messagebox.showerror("输入错误", "请输入或选择一个确定关键词。")
            return
        if not urls:
            messagebox.showerror("输入错误", "请至少提供一个 SERP 落地页 URL。")
            return
        if run is None:
            messagebox.showerror("缺少运行目录", "请先在关键词 Agent 中生成候选词。")
            return
        candidate = self.state.candidate_output
        selected_urls = urls[: self.limit_var.get()]
        self.output.set_text("正在抓取竞品页面并提取标题、H1-H3、FAQ、表格和正文…")

        def worker(progress):
            """抓取竞品页面、分析证据并写入关键词运行目录。"""
            try:
                output = analyze_serp_competitors(
                    keyword=keyword,
                    urls=selected_urls,
                    business_text=candidate.business_text if candidate else "",
                    existing_pages=candidate.existing_pages if candidate else [],
                    max_pages=len(selected_urls),
                    mock=self.state.mock_mode,
                    progress=progress,
                )
                paths = write_competitor_output(output, run)
                return output, paths
            except Exception as error:
                run.update_run(status="failed", current_stage="competitor", error=str(error))
                raise

        self.task_runner.start(worker, on_success=self._finish, on_error=self._fail)

    def _finish(self, result) -> None:
        """显示报告、保存共享竞品结果并更新状态栏。"""
        output, (_report_json, report_md) = result
        self.state.set_competitor_output(output)
        self.output.set_text(render_competitor_report(output))
        success = sum(not page.error and bool(page.text) for page in output.pages)
        self.set_status(
            f"竞品分析完成：成功抓取 {success}/{len(output.pages)}；输出：{report_md.parent}"
        )

    def _set_busy(self, busy: bool) -> None:
        """根据全局任务状态控制竞品运行按钮。"""
        enabled = not busy and self.state.keyword_run is not None
        self.run_button.configure(state="normal" if enabled else "disabled")

    def _fail(self, error: Exception) -> None:
        """显示竞品分析错误并保留此前共享状态。"""
        self.output.set_text("竞品分析失败：\n" + str(error))
        self.set_status("竞品分析失败")
