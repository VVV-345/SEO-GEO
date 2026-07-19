"""关键词 Agent 独立 Tkinter 界面。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from app import (
    build_selected_keyword_output,
    create_run_context,
    fetch_keyword_serp,
    generate_keyword_candidates,
    render_candidate_report,
    render_keyword_report,
    write_candidate_output,
    write_selected_keyword_output,
)
from ui.app_state import AppState
from ui.task_runner import TaskRunner
from ui.widgets import FileSelector, ReadOnlyText


INTENT_LABELS = {
    "transaction": "明确采购/咨询",
    "commercial": "选型/对比",
    "solution": "解决方案",
    "informational": "知识了解",
}


class KeywordAgentView(ttk.Frame):
    """两阶段关键词界面：先人工选候选，再按需获取 SERP URL。"""

    title = "关键词 Agent"

    def __init__(
        self,
        parent,
        *,
        state: AppState,
        task_runner: TaskRunner,
        set_status: Callable[[str], None],
    ) -> None:
        """保存共享依赖并创建关键词模块控件。"""
        super().__init__(parent, padding=10)
        self.state = state
        self.task_runner = task_runner
        self.set_status = set_status
        self.checked_keywords: set[str] = set()
        self.seed_var = tk.StringVar(value="企业知识库")
        self.mock_var = tk.BooleanVar(value=state.mock_mode)
        self.num_var = tk.IntVar(value=20)
        self._build()
        self.mock_var.trace_add("write", self._on_mock_changed)
        self.state.subscribe("mock_mode_changed", self._sync_mock_mode)
        self.task_runner.subscribe_busy(self._set_busy)

    def _build(self) -> None:
        """创建输入表单、候选树、控制按钮和结果区。"""
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(self, text="输入", padding=10)
        right = ttk.LabelFrame(self, text="候选词选择与结果", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(7, weight=1)
        left.rowconfigure(9, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=2)
        right.rowconfigure(4, weight=3)

        ttk.Label(left, text="种子词（空格分隔）").grid(row=0, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.seed_var).grid(row=1, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="需求描述（研究目标、重点方向、排除项）").grid(row=2, column=0, sticky="w")
        self.requirement = tk.Text(left, height=4, wrap="word")
        self.requirement.grid(row=3, column=0, sticky="ew", pady=(3, 10))

        ttk.Label(left, text="业务资料文件（可添加或删除）").grid(row=4, column=0, sticky="w")
        self.file_selector = FileSelector(left, height=4, title="选择关键词业务资料")
        self.file_selector.grid(row=5, column=0, sticky="ew", pady=(3, 10))
        ttk.Label(left, text="补充业务信息（可选）").grid(row=6, column=0, sticky="w")
        self.business = tk.Text(left, height=7, wrap="word")
        self.business.grid(row=7, column=0, sticky="nsew", pady=(3, 10))
        ttk.Label(left, text="客户已有页面 URL（每行一个，可选）").grid(row=8, column=0, sticky="sw")
        self.pages = tk.Text(left, height=7, wrap="none")
        self.pages.grid(row=9, column=0, sticky="nsew", pady=(3, 10))

        controls = ttk.Frame(left)
        controls.grid(row=10, column=0, sticky="ew")
        ttk.Checkbutton(controls, text="Mock 离线测试", variable=self.mock_var).pack(side="left")
        ttk.Label(controls, text="候选数").pack(side="left", padx=(15, 4))
        ttk.Spinbox(controls, from_=1, to=100, width=5, textvariable=self.num_var).pack(side="left")
        self.generate_button = ttk.Button(controls, text="1. 生成候选词", command=self._start_generate)
        self.generate_button.pack(side="right")

        ttk.Label(right, text="按意图分组；双击候选词或按空格切换勾选", foreground="#555").grid(
            row=0, column=0, sticky="w"
        )
        columns = ("checked", "keyword", "intent", "business", "status")
        self.candidate_tree = ttk.Treeview(right, columns=columns, show="tree headings", selectmode="browse")
        self.candidate_tree.heading("#0", text="分类")
        self.candidate_tree.heading("checked", text="选择")
        self.candidate_tree.heading("keyword", text="拓展词")
        self.candidate_tree.heading("intent", text="搜索意图")
        self.candidate_tree.heading("business", text="业务评分")
        self.candidate_tree.heading("status", text="URL 状态")
        self.candidate_tree.column("#0", width=125, stretch=False)
        self.candidate_tree.column("checked", width=55, anchor="center", stretch=False)
        self.candidate_tree.column("keyword", width=230)
        self.candidate_tree.column("intent", width=105, stretch=False)
        self.candidate_tree.column("business", width=135, stretch=False)
        self.candidate_tree.column("status", width=150)
        self.candidate_tree.grid(row=1, column=0, sticky="nsew", pady=(4, 6))
        self.candidate_tree.bind("<Double-1>", self._toggle_current)
        self.candidate_tree.bind("<space>", self._toggle_current)
        self.candidate_tree.bind("<<TreeviewSelect>>", self._show_current_candidate)

        query_buttons = ttk.Frame(right)
        query_buttons.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.fetch_button = ttk.Button(
            query_buttons, text="2. 获取勾选词 URL", command=self._start_fetch_selected, state="disabled"
        )
        self.fetch_button.pack(side="left")
        self.retry_button = ttk.Button(
            query_buttons, text="重试当前词 URL", command=self._start_retry_current, state="disabled"
        )
        self.retry_button.pack(side="left", padx=(8, 0))
        ttk.Button(query_buttons, text="全不选", command=self._clear_checks).pack(side="right")
        ttk.Label(right, text="候选信息 / 对应 URL 结果", foreground="#555").grid(row=3, column=0, sticky="w")
        self.output = ReadOnlyText(right)
        self.output.grid(row=4, column=0, sticky="nsew", pady=(3, 0))

    def _on_mock_changed(self, *_args) -> None:
        """把关键词页 Mock 开关同步到跨 Agent 状态。"""
        self.state.set_mock_mode(self.mock_var.get())

    def _sync_mock_mode(self, enabled: bool) -> None:
        """接收其他模块或主窗口发出的 Mock 状态变化。"""
        if self.mock_var.get() != bool(enabled):
            self.mock_var.set(bool(enabled))

    def _start_generate(self) -> None:
        """校验输入、创建运行目录，并启动候选生成任务。"""
        seeds = self.seed_var.get().split()
        if not seeds:
            messagebox.showerror("输入错误", "请至少输入一个种子词。")
            return
        urls = [line.strip() for line in self.pages.get("1.0", "end").splitlines() if line.strip()]
        run = create_run_context(seeds)
        args = {
            "seeds": seeds,
            "requirement": self.requirement.get("1.0", "end").strip(),
            "material_files": self.file_selector.get_paths(),
            "page_urls": urls,
            "inline_business_text": self.business.get("1.0", "end").strip(),
            "candidate_limit": self.num_var.get(),
            "mock": self.state.mock_mode,
        }
        self.output.set_text("正在生成候选词；此阶段不会查询自然结果 URL…")

        def worker(progress):
            """生成并保存候选词，返回主线程需要的结果。"""
            try:
                output = generate_keyword_candidates(**args, progress=progress)
                paths = write_candidate_output(output, run)
                return output, run, paths
            except Exception as error:
                run.update_run(status="failed", current_stage="keyword_candidates", error=str(error))
                raise

        self.task_runner.start(worker, on_success=self._finish_candidates, on_error=self._fail)

    def _finish_candidates(self, result) -> None:
        """渲染候选结果并通过 AppState 通知其他 Agent。"""
        output, run, (_candidate_json, candidate_report) = result
        self.checked_keywords.clear()
        self.state.set_keyword_candidates(output, run)
        self._populate_candidates()
        self.output.set_text(render_candidate_report(output))
        self.set_status(
            f"候选生成完成：{len(output.candidates)} 个；输出：{candidate_report.parent}；请勾选后获取 URL"
        )
        self.fetch_button.configure(state="normal")

    def _populate_candidates(self) -> None:
        """按搜索意图重建候选树，同时保留查询和勾选状态。"""
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        output = self.state.candidate_output
        if output is None:
            return
        for intent in ("transaction", "commercial", "solution", "informational"):
            items = [item for item in output.candidates if item.intent == intent]
            if not items:
                continue
            group = self.candidate_tree.insert(
                "", "end", text=f"{INTENT_LABELS[intent]}（{len(items)}）", open=True
            )
            for item in items:
                score = f"{item.business_fit}/{item.commercial_proximity}/{item.specificity}"
                self.candidate_tree.insert(
                    group,
                    "end",
                    iid=f"kw:{item.keyword}",
                    values=(
                        "[x]" if item.keyword in self.checked_keywords else "[ ]",
                        item.keyword,
                        INTENT_LABELS.get(item.intent, item.intent),
                        score,
                        self._serp_status(item.keyword),
                    ),
                )

    def _serp_status(self, keyword: str) -> str:
        """返回候选表格使用的简短 SERP 状态。"""
        result = self.state.serp_results.get(keyword)
        if result is None:
            return "未查询"
        return f"已获取 {len(result.results)} 条" if result.results else "获取失败，可重试"

    def _toggle_current(self, _event=None) -> str:
        """切换当前候选词的人工勾选状态。"""
        item_id = self.candidate_tree.focus()
        if not item_id.startswith("kw:"):
            return "break"
        keyword = item_id[3:]
        if keyword in self.checked_keywords:
            self.checked_keywords.remove(keyword)
        else:
            self.checked_keywords.add(keyword)
        values = list(self.candidate_tree.item(item_id, "values"))
        values[0] = "[x]" if keyword in self.checked_keywords else "[ ]"
        self.candidate_tree.item(item_id, values=values)
        return "break"

    def _clear_checks(self) -> None:
        """清空人工勾选，不删除已查询的 SERP 数据。"""
        self.checked_keywords.clear()
        self._populate_candidates()

    def _show_current_candidate(self, _event=None) -> None:
        """展示当前候选的业务信息和对应 SERP 证据。"""
        item_id = self.candidate_tree.focus()
        self.retry_button.configure(state="normal" if item_id.startswith("kw:") else "disabled")
        output = self.state.candidate_output
        if not item_id.startswith("kw:") or output is None:
            return
        keyword = item_id[3:]
        candidate = next((item for item in output.candidates if item.keyword == keyword), None)
        if candidate is None:
            return
        result = self.state.serp_results.get(keyword)
        lines = [
            f"拓展词：{candidate.keyword}",
            f"意图：{INTENT_LABELS.get(candidate.intent, candidate.intent)}",
            f"业务评分：匹配 {candidate.business_fit}/5 ｜ 商业接近 {candidate.commercial_proximity}/5 ｜ 具体度 {candidate.specificity}/5",
            f"近义词：{'、'.join(candidate.variants) if candidate.variants else '无'}",
            f"百度下拉词：{'、'.join(candidate.suggestions) if candidate.suggestions else '未获取'}",
        ]
        if result is None:
            lines.append("URL：尚未查询；勾选后点击“获取勾选词 URL”。")
        else:
            lines.append(f"百度相关搜索：{'、'.join(result.related_searches) if result.related_searches else '未获取'}")
            lines.append(f"URL（{len(result.results)}）：")
            lines.extend(f"  {index}. {item.url}" for index, item in enumerate(result.results, 1))
            if not result.results:
                lines.append(f"  获取失败：{result.error or '未取得自然结果'}")
            if result.filtered_results:
                lines.append(f"已过滤 URL（{len(result.filtered_results)}）：")
                lines.extend(f"  - {item.url}（{item.reason}）" for item in result.filtered_results)
        self.output.set_text("\n".join(lines))

    def _start_fetch_selected(self) -> None:
        """收集人工勾选词并启动按需 SERP 查询。"""
        output = self.state.candidate_output
        if output is None:
            return
        keywords = [item.keyword for item in output.candidates if item.keyword in self.checked_keywords]
        if not keywords:
            messagebox.showinfo("尚未勾选", "请双击候选词或按空格勾选至少一个词。")
            return
        self._start_fetch(keywords)

    def _start_retry_current(self) -> None:
        """只重新查询当前候选词。"""
        item_id = self.candidate_tree.focus()
        if item_id.startswith("kw:"):
            self._start_fetch([item_id[3:]])

    def _start_fetch(self, keywords: list[str]) -> None:
        """启动指定关键词列表的 SERP 查询与机会排序。"""
        candidate_output = self.state.candidate_output
        run = self.state.keyword_run
        if candidate_output is None or run is None:
            messagebox.showerror("缺少运行上下文", "请先重新生成候选词。")
            return
        self.set_status(f"只查询所选的 {len(keywords)} 个关键词…")

        def worker(progress):
            """查询所选词、合并已有结果并保存最终关键词输出。"""
            try:
                fetched = fetch_keyword_serp(
                    keywords, mock=self.state.mock_mode, progress=progress
                )
                merged = dict(self.state.serp_results)
                merged.update(fetched)
                selected = [
                    item.keyword for item in candidate_output.candidates if item.keyword in merged
                ]
                output = build_selected_keyword_output(
                    candidate_output,
                    selected,
                    merged,
                    mock=self.state.mock_mode,
                    progress=progress,
                )
                paths = write_selected_keyword_output(output, merged, run)
                return merged, output, paths
            except Exception as error:
                run.update_run(status="failed", current_stage="keyword_serp", error=str(error))
                raise

        self.task_runner.start(worker, on_success=self._finish_fetch, on_error=self._fail)

    def _finish_fetch(self, result) -> None:
        """刷新关键词报告并发布新的 SERP 共享状态。"""
        merged, output, (_serp_path, _opportunities_path, report_path) = result
        self.state.set_serp_results(merged, output)
        self._populate_candidates()
        self.output.set_text(render_keyword_report(output))
        success = sum(bool(item.results) for item in merged.values())
        self.set_status(
            f"URL 查询完成：成功 {success}/{len(merged)}；输出：{report_path.parent}；失败词可单独重试"
        )

    def _set_busy(self, busy: bool) -> None:
        """根据全局任务状态控制关键词操作按钮。"""
        self.generate_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.fetch_button.configure(state="disabled")
            self.retry_button.configure(state="disabled")
        elif self.state.candidate_output is not None:
            self.fetch_button.configure(state="normal")
            focused = self.candidate_tree.focus()
            self.retry_button.configure(state="normal" if focused.startswith("kw:") else "disabled")

    def _fail(self, error: Exception) -> None:
        """显示关键词模块任务错误并保留此前状态。"""
        self.output.set_text("运行失败：\n" + str(error))
        self.set_status("关键词 Agent 运行失败")

