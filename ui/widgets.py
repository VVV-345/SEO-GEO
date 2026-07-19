"""所有 Agent UI 可复用的轻量 Tkinter 控件。"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk


SUPPORTED_MATERIALS = "*.pdf *.docx *.txt *.md *.html *.htm *.json *.csv *.yaml *.yml"


class ReadOnlyText(tk.Text):
    """提供统一只读写入接口的多行文本框。"""

    def __init__(self, parent, **kwargs) -> None:
        """创建默认自动换行且只读的文本框。"""
        kwargs.setdefault("wrap", "word")
        super().__init__(parent, **kwargs)
        self.configure(state="disabled")

    def set_text(self, value: str) -> None:
        """临时解锁、替换完整内容后恢复只读状态。"""
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", value)
        self.configure(state="disabled")

    def append_text(self, value: str) -> None:
        """追加文本并自动滚动到底部。"""
        self.configure(state="normal")
        self.insert("end", value)
        self.see("end")
        self.configure(state="disabled")


class FileSelector(ttk.Frame):
    """带添加、删除和路径去重逻辑的资料文件选择器。"""

    def __init__(self, parent, *, height: int = 4, title: str = "选择业务资料") -> None:
        """创建文件列表和增删按钮。"""
        super().__init__(parent)
        self.dialog_title = title
        self.paths: list[str] = []
        self.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(self, height=height, selectmode="extended")
        self.listbox.grid(row=0, column=0, rowspan=2, sticky="ew")
        ttk.Button(self, text="添加资料", command=self.add_files).grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )
        ttk.Button(self, text="删除选中", command=self.remove_selected).grid(
            row=1, column=1, sticky="ew", padx=(6, 0)
        )

    def add_files(self) -> None:
        """打开文件对话框并加入尚未存在的资料路径。"""
        chosen = filedialog.askopenfilenames(
            title=self.dialog_title,
            filetypes=[("支持的资料", SUPPORTED_MATERIALS), ("所有文件", "*.*")],
        )
        for path in chosen:
            if path not in self.paths:
                self.paths.append(path)
                self.listbox.insert("end", path)

    def remove_selected(self) -> None:
        """同步删除列表中选中的路径。"""
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)
            del self.paths[index]

    def get_paths(self) -> list[str]:
        """返回当前资料路径副本，避免调用方修改内部列表。"""
        return list(self.paths)


class PlaceholderView(ttk.Frame):
    """尚未实现 Agent 使用的统一占位标签页。"""

    def __init__(self, parent, *, name: str) -> None:
        """显示简短的预留说明。"""
        super().__init__(parent, padding=20)
        ttk.Label(self, text=f"{name} Agent：预留，尚未实现。", foreground="#666").pack(anchor="w")

