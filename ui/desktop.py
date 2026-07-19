from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app import (
    analyze_serp_competitors,
    build_selected_keyword_output,
    create_run_context,
    fetch_keyword_serp,
    generate_keyword_candidates,
    render_candidate_report,
    render_keyword_report,
    render_competitor_report,
    write_candidate_output,
    write_selected_keyword_output,
    write_competitor_output,
)
from tools.progress import ProgressEvent, ProgressReporter


INTENT_LABELS = {
    "transaction": "明确采购/咨询",
    "commercial": "选型/对比",
    "solution": "解决方案",
    "informational": "知识了解",
}


class SEOAgentUI(tk.Tk):
    """SEO/GEO 桌面工作台，当前串联关键词选择与单词竞品分析。"""

    def __init__(self) -> None:
        """初始化界面状态、当前运行上下文和跨标签页共享结果。"""
        super().__init__()
        self.title("SEO/GEO Agents")
        self.geometry("1320x860")
        self.minsize(1050, 700)
        self.selected_files: list[str] = []
        self.candidate_output = None
        self.run_context = None
        self.serp_results = {}
        self.checked_keywords: set[str] = set()
        self.seed_var = tk.StringVar(value="企业知识库")
        self.mock_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.IntVar(value=0)
        self.num_var = tk.IntVar(value=20)
        self.competitor_keyword_var = tk.StringVar()
        self.competitor_limit_var = tk.IntVar(value=10)
        self._build()

    def _build(self) -> None:
        """创建 Agent 标签页、全局状态栏和进度条。"""
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
        competitor_tab = ttk.Frame(tabs, padding=10)
        tabs.add(competitor_tab, text="SERP + 竞品")
        self._build_competitor_tab(competitor_tab)
        for name in ("技术 SEO", "Content Brief", "写作", "SEO/GEO 质检"):
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
        """创建关键词 Agent 的输入、人工选词、结果和进度区域。"""
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

    def _build_competitor_tab(self, tab: ttk.Frame) -> None:
        """创建单关键词竞品分析界面；URL 可从关键词结果带入，也可人工修改。"""
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=3)
        tab.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(tab, text="分析输入", padding=10)
        right = ttk.LabelFrame(tab, text="竞品分析输出", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(left, text="确定关键词（每次只分析一个）").grid(row=0, column=0, sticky="w")
        self.competitor_keyword = ttk.Combobox(left, textvariable=self.competitor_keyword_var)
        self.competitor_keyword.grid(row=1, column=0, sticky="ew", pady=(3, 10))
        self.competitor_keyword.bind("<<ComboboxSelected>>", self._load_competitor_urls)
        ttk.Label(left, text="SERP 落地页 URL（每行一个，可增删；最多 10 个）").grid(
            row=2, column=0, sticky="w"
        )
        self.competitor_urls = tk.Text(left, wrap="none")
        self.competitor_urls.grid(row=3, column=0, sticky="nsew", pady=(3, 10))

        controls = ttk.Frame(left)
        controls.grid(row=4, column=0, sticky="ew")
        ttk.Label(controls, text="抓取页数").pack(side="left")
        ttk.Spinbox(
            controls, from_=1, to=10, width=5, textvariable=self.competitor_limit_var
        ).pack(side="left", padx=(5, 0))
        self.competitor_button = ttk.Button(
            controls, text="开始 SERP + 竞品分析", command=self._start_competitor, state="disabled"
        )
        self.competitor_button.pack(side="right")
        ttk.Label(
            left,
            text="使用当前运行的业务资料、已有页面和 Mock 设置；真实模式会逐个访问上述网页。",
            foreground="#666",
            wraplength=420,
        ).grid(row=5, column=0, sticky="w", pady=(10, 0))

        ttk.Label(right, text="共同主题、FAQ、内容缺口、必写项和建议结构", foreground="#555").grid(
            row=0, column=0, sticky="w"
        )
        self.competitor_output = tk.Text(right, wrap="word", state="disabled")
        self.competitor_output.grid(row=1, column=0, sticky="nsew", pady=(4, 0))

    def _add_files(self) -> None:
        """让用户一次添加多个业务资料文件，并避免列表中出现重复路径。"""
        chosen = filedialog.askopenfilenames(
            title="选择客户业务资料",
            filetypes=[("支持的资料", "*.pdf *.docx *.txt *.md *.html *.htm *.json *.csv *.yaml *.yml"), ("所有文件", "*.*")],
        )
        for path in chosen:
            if path not in self.selected_files:
                self.selected_files.append(path)
                self.files_list.insert("end", path)

    def _remove_files(self) -> None:
        """从界面和内部路径列表中同步删除选中的资料文件。"""
        for index in reversed(self.files_list.curselection()):
            self.files_list.delete(index)
            del self.selected_files[index]

    def _start_generate(self) -> None:
        """校验输入、创建本次运行目录，并在后台生成候选词。"""
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
        run = create_run_context(seeds)
        args = (*args, run)
        self.run_context = run
        self._set_busy(True)
        self.progress_var.set(0)
        self._set_timeline("")
        self._set_output("正在生成候选词；此阶段不会查询自然结果 URL…")
        threading.Thread(target=self._generate_thread, args=args, daemon=True).start()

    def _generate_thread(self, seeds, requirement, business, files, urls, limit, mock, run) -> None:
        """后台执行候选生成，避免网络和模型调用阻塞 Tkinter 主线程。"""
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
            candidate_json, candidate_report = write_candidate_output(output, run)
            self.after(0, lambda: self._finish_candidates(output, candidate_json, candidate_report))
        except Exception as error:
            run.update_run(status="failed", current_stage="keyword_candidates", error=str(error))
            self.after(0, lambda: self._fail(str(error)))

    def _finish_candidates(self, output, candidate_json: Path, candidate_report: Path) -> None:
        """在主线程渲染候选结果，并开放人工选择和 URL 查询。"""
        self.candidate_output = output
        self.serp_results = {}
        self.checked_keywords.clear()
        self._populate_candidates()
        self._set_output(render_candidate_report(output))
        self.status_var.set(
            f"候选生成完成：{len(output.candidates)} 个；输出：{candidate_report.parent}；请勾选后获取URL"
        )
        self.progress_var.set(100)
        self._set_busy(False)
        self.fetch_button.configure(state="normal")

    def _populate_candidates(self) -> None:
        """按搜索意图重建候选树，同时保留勾选与 URL 查询状态。"""
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
        """返回候选表格中简短、可重试的 SERP 状态文案。"""
        result = self.serp_results.get(keyword)
        if result is None:
            return "未查询"
        if result.results:
            return f"已获取 {len(result.results)} 条"
        return "获取失败，可重试"

    def _toggle_current(self, _event=None) -> str:
        """切换当前候选的人工勾选状态，供双击和空格键复用。"""
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
        """清空全部人工选择，但不删除已经查询到的 SERP 数据。"""
        self.checked_keywords.clear()
        for group in self.candidate_tree.get_children():
            for item_id in self.candidate_tree.get_children(group):
                values = list(self.candidate_tree.item(item_id, "values"))
                values[0] = "[ ]"
                self.candidate_tree.item(item_id, values=values)

    def _show_current_candidate(self, _event=None) -> None:
        """展示当前候选的业务信息、下拉词、相关搜索和落地页 URL。"""
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
            if result.filtered_results:
                lines.append(f"已过滤 URL（{len(result.filtered_results)}）：")
                lines.extend(
                    f"  - {item.url}（{item.reason}）" for item in result.filtered_results
                )
        self._set_output("\n".join(lines))

    def _start_fetch_selected(self) -> None:
        """收集已勾选关键词并启动按需 SERP 查询。"""
        if self.candidate_output is None:
            return
        keywords = [item.keyword for item in self.candidate_output.candidates if item.keyword in self.checked_keywords]
        if not keywords:
            messagebox.showinfo("尚未勾选", "请双击候选词或按空格勾选至少一个词。")
            return
        self._start_fetch(keywords)

    def _start_retry_current(self) -> None:
        """只重新查询当前候选，避免一个失败词导致整批重跑。"""
        item_id = self.candidate_tree.focus()
        if item_id.startswith("kw:"):
            self._start_fetch([item_id[3:]])

    def _start_fetch(self, keywords: list[str]) -> None:
        """禁用可冲突操作，并在后台查询指定关键词列表。"""
        self._set_busy(True)
        self.status_var.set(f"只查询所选的 {len(keywords)} 个关键词…")
        threading.Thread(target=self._fetch_thread, args=(keywords, self.mock_var.get()), daemon=True).start()

    def _fetch_thread(self, keywords: list[str], mock: bool) -> None:
        """后台获取 URL、重新评分并写入当前运行目录。"""
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
            if self.run_context is None:
                raise RuntimeError("当前没有运行目录，请重新生成候选词。")
            serp_path, opportunities_path, report_path = write_selected_keyword_output(
                output, merged, self.run_context
            )
            self.after(
                0,
                lambda: self._finish_fetch(merged, output, serp_path, opportunities_path, report_path),
            )
        except Exception as error:
            if self.run_context is not None:
                self.run_context.update_run(status="failed", current_stage="keyword_serp", error=str(error))
            self.after(0, lambda: self._fail(str(error)))

    def _finish_fetch(
        self,
        merged,
        output,
        serp_path: Path,
        opportunities_path: Path,
        report_path: Path,
    ) -> None:
        """刷新关键词结果并把可用关键词同步给竞品分析标签页。"""
        self.serp_results = merged
        self._populate_candidates()
        self._set_output(render_keyword_report(output))
        success = sum(bool(result.results) for result in merged.values())
        self.status_var.set(
            f"URL查询完成：成功 {success}/{len(merged)}；输出：{report_path.parent}；失败词可单独重试"
        )
        self.progress_var.set(100)
        self._set_busy(False)
        self.fetch_button.configure(state="normal")
        self.retry_button.configure(state="normal" if self.candidate_tree.focus().startswith("kw:") else "disabled")
        self._refresh_competitor_choices()

    def _refresh_competitor_choices(self) -> None:
        """把已有 SERP URL 的关键词加入竞品下拉框，并默认选择第一个。"""
        keywords = [keyword for keyword, result in self.serp_results.items() if result.results]
        self.competitor_keyword.configure(values=keywords)
        if keywords and self.competitor_keyword_var.get() not in keywords:
            self.competitor_keyword_var.set(keywords[0])
            self._load_competitor_urls()
        self.competitor_button.configure(
            state="normal" if self.run_context is not None else "disabled"
        )

    def _load_competitor_urls(self, _event=None) -> None:
        """将选定关键词的 SERP 落地页载入文本框，供用户复核和增删。"""
        result = self.serp_results.get(self.competitor_keyword_var.get())
        if result is None:
            return
        urls = "\n".join(item.url for item in result.results)
        self.competitor_urls.delete("1.0", "end")
        self.competitor_urls.insert("1.0", urls)

    def _start_competitor(self) -> None:
        """校验单关键词和 URL 列表，然后启动竞品页面抓取与模型分析。"""
        keyword = self.competitor_keyword_var.get().strip()
        urls = list(dict.fromkeys(
            line.strip() for line in self.competitor_urls.get("1.0", "end").splitlines() if line.strip()
        ))
        if not keyword:
            messagebox.showerror("输入错误", "请输入或选择一个确定关键词。")
            return
        if not urls:
            messagebox.showerror("输入错误", "请至少提供一个 SERP 落地页 URL。")
            return
        if self.run_context is None:
            messagebox.showerror("缺少运行目录", "请先在关键词 Agent 中生成一次候选词。")
            return
        self._set_busy(True)
        self.competitor_button.configure(state="disabled")
        self._set_competitor_output("正在抓取竞品页面并提取标题、H1-H3、FAQ、表格和正文…")
        threading.Thread(
            target=self._competitor_thread,
            args=(keyword, urls[: self.competitor_limit_var.get()], self.mock_var.get()),
            daemon=True,
        ).start()

    def _competitor_thread(self, keyword: str, urls: list[str], mock: bool) -> None:
        """后台执行竞品 Agent，并沿用候选阶段的业务资料和已有页面。"""
        try:
            if self.run_context is None:
                raise RuntimeError("当前运行目录已经失效，请重新生成候选词。")
            progress = ProgressReporter([self._queue_progress])
            candidate = self.candidate_output
            output = analyze_serp_competitors(
                keyword=keyword,
                urls=urls,
                business_text=candidate.business_text if candidate else "",
                existing_pages=candidate.existing_pages if candidate else [],
                max_pages=len(urls),
                mock=mock,
                progress=progress,
            )
            report_json, report_md = write_competitor_output(output, self.run_context)
            self.after(0, lambda: self._finish_competitor(output, report_json, report_md))
        except Exception as error:
            if self.run_context is not None:
                self.run_context.update_run(status="failed", current_stage="competitor", error=str(error))
            self.after(0, lambda: self._fail_competitor(str(error)))

    def _finish_competitor(self, output, report_json: Path, report_md: Path) -> None:
        """在主线程展示竞品报告和实际输出位置。"""
        self._set_competitor_output(render_competitor_report(output))
        success = sum(not page.error and bool(page.text) for page in output.pages)
        self.status_var.set(
            f"竞品分析完成：成功抓取 {success}/{len(output.pages)}；输出：{report_md.parent}"
        )
        self.progress_var.set(100)
        self._set_busy(False)
        self.competitor_button.configure(state="normal")

    def _fail_competitor(self, error: str) -> None:
        """显示竞品 Agent 错误并恢复界面按钮。"""
        self._set_competitor_output("竞品分析失败：\n" + error)
        self.status_var.set("竞品分析失败")
        self._set_busy(False)
        self.competitor_button.configure(state="normal")

    def _set_busy(self, busy: bool) -> None:
        """统一控制可能产生并发工作流的按钮状态。"""
        self.generate_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.fetch_button.configure(state="disabled")
            self.retry_button.configure(state="disabled")
            self.competitor_button.configure(state="disabled")

    def _fail(self, error: str) -> None:
        """处理关键词流程异常，保留已有结果并恢复可继续操作状态。"""
        self._set_output("运行失败：\n" + error)
        self.status_var.set("运行失败")
        self._set_busy(False)
        if self.candidate_output is not None:
            self.fetch_button.configure(state="normal")

    def _queue_progress(self, event: ProgressEvent) -> None:
        """把后台线程的进度事件安全调度到 Tkinter 主线程。"""
        self.after(0, lambda: self._show_progress(event))

    def _show_progress(self, event: ProgressEvent) -> None:
        """将通用进度事件渲染为状态栏、进度条和时间线。"""
        suffix = f"（{event.current}/{event.total}）" if event.current is not None and event.total else ""
        self.status_var.set(f"{event.label}：{event.message}{suffix}")
        if event.percent is not None:
            self.progress_var.set(event.percent)
        self.timeline.configure(state="normal")
        self.timeline.insert("end", f"[{event.status}] {event.label}：{event.message}{suffix}\n")
        self.timeline.see("end")
        self.timeline.configure(state="disabled")

    def _set_output(self, value: str) -> None:
        """以只读方式替换关键词输出文本。"""
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", value)
        self.output.configure(state="disabled")

    def _set_timeline(self, value: str) -> None:
        """以只读方式替换通用工作流执行时间线。"""
        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        self.timeline.insert("1.0", value)
        self.timeline.configure(state="disabled")

    def _set_competitor_output(self, value: str) -> None:
        """以只读方式替换竞品分析输出文本。"""
        self.competitor_output.configure(state="normal")
        self.competitor_output.delete("1.0", "end")
        self.competitor_output.insert("1.0", value)
        self.competitor_output.configure(state="disabled")


def launch() -> None:
    """启动桌面工作台并进入 Tkinter 事件循环。"""
    SEOAgentUI().mainloop()
