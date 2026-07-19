from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import (
    build_selected_keyword_output,
    fetch_keyword_serp,
    generate_keyword_candidates,
    render_candidate_report,
    render_keyword_report,
    write_keyword_output,
)
from tools.progress import ProgressEvent, ProgressReporter


INTENT_LABELS = {
    "transaction": "明确采购/咨询",
    "commercial": "选型/对比",
    "solution": "解决方案",
    "informational": "知识了解",
}


class SEOAgentUI(tk.Tk):
    """两阶段关键词工作台：先生成并勾选候选词，再按需查询SERP URL。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("SEO/GEO Agents")
        self.geometry("1320x860")
        self.minsize(1050, 700)
        self.selected_files: list[str] = []
        self.candidate_output = None
        self.serp_results = {}
        self.checked_keywords: set[str] = set()
        self.seed_var = tk.StringVar(value="企业知识库")
        self.mock_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.IntVar(value=0)
        self.num_var = tk.IntVar(value=20)
        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        ttk.Label(root, text="SEO/GEO 多 Agent 工作台", font=("Microsoft YaHei UI", 17, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        tabs = ttk.Notebook(root)
        tabs.grid(row=1, column=0, sticky="nsew")
        keyword_tab = ttk.Frame(tabs, padding=10)
        tabs.add(keyword_tab, text="关键词 Agent")
        for name in ("SERP + 竞品", "技术 SEO", "Content Brief", "写作", "SEO/GEO 质检"):
            tab = ttk.Frame(tabs, padding=20)
            ttk.Label(tab, text=f"{name} Agent：预留，尚未实现。", foreground="#666").pack(anchor="w")
            tabs.add(tab, text=name)
        self._build_keyword_tab(keyword_tab)

        status = ttk.Frame(root)
        status.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Progressbar(status, maximum=100, variable=self.progress_var, length=280).grid(row=0, column=1, sticky="e")

    def _build_keyword_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=3)
        tab.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(tab, text="输入", padding=10)
        right = ttk.LabelFrame(tab, text="候选词选择与结果", padding=10)
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
        file_area = ttk.Frame(left)
        file_area.grid(row=5, column=0, sticky="ew", pady=(3, 10))
        file_area.columnconfigure(0, weight=1)
        self.files_list = tk.Listbox(file_area, height=4, selectmode="extended")
        self.files_list.grid(row=0, column=0, rowspan=2, sticky="ew")
        ttk.Button(file_area, text="添加资料", command=self._add_files).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(file_area, text="删除选中", command=self._remove_files).grid(row=1, column=1, sticky="ew", padx=(6, 0))

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
        self.candidate_tree.heading("status", text="URL状态")
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
        self.fetch_button = ttk.Button(query_buttons, text="2. 获取勾选词 URL", command=self._start_fetch_selected, state="disabled")
        self.fetch_button.pack(side="left")
        self.retry_button = ttk.Button(query_buttons, text="重试当前词 URL", command=self._start_retry_current, state="disabled")
        self.retry_button.pack(side="left", padx=(8, 0))
        ttk.Button(query_buttons, text="全不选", command=self._clear_checks).pack(side="right")

        ttk.Label(right, text="候选信息 / 对应URL结果", foreground="#555").grid(row=3, column=0, sticky="w")
        self.output = tk.Text(right, wrap="word", state="disabled")
        self.output.grid(row=4, column=0, sticky="nsew", pady=(3, 6))
        ttk.Label(right, text="执行步骤", foreground="#666").grid(row=5, column=0, sticky="w")
        self.timeline = tk.Text(right, height=6, wrap="word", state="disabled", foreground="#444")
        self.timeline.grid(row=6, column=0, sticky="ew", pady=(2, 0))

    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="选择客户业务资料",
            filetypes=[("支持的资料", "*.pdf *.docx *.txt *.md *.html *.htm *.json *.csv *.yaml *.yml"), ("所有文件", "*.*")],
        )
        for path in chosen:
            if path not in self.selected_files:
                self.selected_files.append(path)
                self.files_list.insert("end", path)

    def _remove_files(self) -> None:
        for index in reversed(self.files_list.curselection()):
            self.files_list.delete(index)
            del self.selected_files[index]

    def _start_generate(self) -> None:
        seeds = self.seed_var.get().split()
        if not seeds:
            messagebox.showerror("输入错误", "请至少输入一个种子词。")
            return
        urls = [line.strip() for line in self.pages.get("1.0", "end").splitlines() if line.strip()]
        args = (
            seeds,
            self.requirement.get("1.0", "end").strip(),
            self.business.get("1.0", "end"),
            list(self.selected_files),
            urls,
            self.num_var.get(),
            self.mock_var.get(),
        )
        self._set_busy(True)
        self.progress_var.set(0)
        self._set_timeline("")
        self._set_output("正在生成候选词；此阶段不会查询自然结果 URL…")
        threading.Thread(target=self._generate_thread, args=args, daemon=True).start()

    def _generate_thread(self, seeds, requirement, business, files, urls, limit, mock) -> None:
        try:
            progress = ProgressReporter([self._queue_progress])
            output = generate_keyword_candidates(
                seeds=seeds,
                requirement=requirement,
                material_files=files,
                page_urls=urls,
                inline_business_text=business,
                candidate_limit=limit,
                mock=mock,
                progress=progress,
            )
            self.after(0, lambda: self._finish_candidates(output))
        except Exception as error:
            self.after(0, lambda: self._fail(str(error)))

    def _finish_candidates(self, output) -> None:
        self.candidate_output = output
        self.serp_results = {}
        self.checked_keywords.clear()
        self._populate_candidates()
        self._set_output(render_candidate_report(output))
        self.status_var.set(f"候选生成完成：{len(output.candidates)} 个；请勾选后获取URL")
        self.progress_var.set(100)
        self._set_busy(False)
        self.fetch_button.configure(state="normal")

    def _populate_candidates(self) -> None:
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        if self.candidate_output is None:
            return
        for intent in ("transaction", "commercial", "solution", "informational"):
            items = [item for item in self.candidate_output.candidates if item.intent == intent]
            if not items:
                continue
            group = self.candidate_tree.insert("", "end", text=f"{INTENT_LABELS[intent]}（{len(items)}）", open=True)
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
        result = self.serp_results.get(keyword)
        if result is None:
            return "未查询"
        if result.results:
            return f"已获取 {len(result.results)} 条"
        return "获取失败，可重试"

    def _toggle_current(self, _event=None) -> str:
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
        self.checked_keywords.clear()
        for group in self.candidate_tree.get_children():
            for item_id in self.candidate_tree.get_children(group):
                values = list(self.candidate_tree.item(item_id, "values"))
                values[0] = "[ ]"
                self.candidate_tree.item(item_id, values=values)

    def _show_current_candidate(self, _event=None) -> None:
        item_id = self.candidate_tree.focus()
        self.retry_button.configure(state="normal" if item_id.startswith("kw:") else "disabled")
        if not item_id.startswith("kw:") or self.candidate_output is None:
            return
        keyword = item_id[3:]
        candidate = next((item for item in self.candidate_output.candidates if item.keyword == keyword), None)
        if candidate is None:
            return
        result = self.serp_results.get(keyword)
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
        self._set_output("\n".join(lines))

    def _start_fetch_selected(self) -> None:
        if self.candidate_output is None:
            return
        keywords = [item.keyword for item in self.candidate_output.candidates if item.keyword in self.checked_keywords]
        if not keywords:
            messagebox.showinfo("尚未勾选", "请双击候选词或按空格勾选至少一个词。")
            return
        self._start_fetch(keywords)

    def _start_retry_current(self) -> None:
        item_id = self.candidate_tree.focus()
        if item_id.startswith("kw:"):
            self._start_fetch([item_id[3:]])

    def _start_fetch(self, keywords: list[str]) -> None:
        self._set_busy(True)
        self.status_var.set(f"只查询所选的 {len(keywords)} 个关键词…")
        threading.Thread(target=self._fetch_thread, args=(keywords, self.mock_var.get()), daemon=True).start()

    def _fetch_thread(self, keywords: list[str], mock: bool) -> None:
        try:
            progress = ProgressReporter([self._queue_progress])
            fetched = fetch_keyword_serp(keywords, mock=mock, progress=progress)
            merged = dict(self.serp_results)
            merged.update(fetched)
            output = build_selected_keyword_output(
                self.candidate_output,
                [item.keyword for item in self.candidate_output.candidates if item.keyword in merged],
                merged,
                mock=mock,
                progress=progress,
            )
            json_path, markdown_path = write_keyword_output(output)
            self.after(0, lambda: self._finish_fetch(merged, output, json_path, markdown_path))
        except Exception as error:
            self.after(0, lambda: self._fail(str(error)))

    def _finish_fetch(self, merged, output, json_path: Path, markdown_path: Path) -> None:
        self.serp_results = merged
        self._populate_candidates()
        self._set_output(render_keyword_report(output))
        success = sum(bool(result.results) for result in merged.values())
        self.status_var.set(f"URL查询完成：成功 {success}/{len(merged)}；失败词可选中后单独重试")
        self.progress_var.set(100)
        self._set_busy(False)
        self.fetch_button.configure(state="normal")
        self.retry_button.configure(state="normal" if self.candidate_tree.focus().startswith("kw:") else "disabled")

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.fetch_button.configure(state="disabled")
            self.retry_button.configure(state="disabled")

    def _fail(self, error: str) -> None:
        self._set_output("运行失败：\n" + error)
        self.status_var.set("运行失败")
        self._set_busy(False)
        if self.candidate_output is not None:
            self.fetch_button.configure(state="normal")

    def _queue_progress(self, event: ProgressEvent) -> None:
        self.after(0, lambda: self._show_progress(event))

    def _show_progress(self, event: ProgressEvent) -> None:
        suffix = f"（{event.current}/{event.total}）" if event.current is not None and event.total else ""
        self.status_var.set(f"{event.label}：{event.message}{suffix}")
        if event.percent is not None:
            self.progress_var.set(event.percent)
        self.timeline.configure(state="normal")
        self.timeline.insert("end", f"[{event.status}] {event.label}：{event.message}{suffix}\n")
        self.timeline.see("end")
        self.timeline.configure(state="disabled")

    def _set_output(self, value: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", value)
        self.output.configure(state="disabled")

    def _set_timeline(self, value: str) -> None:
        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        self.timeline.insert("1.0", value)
        self.timeline.configure(state="disabled")


def launch() -> None:
    SEOAgentUI().mainloop()
