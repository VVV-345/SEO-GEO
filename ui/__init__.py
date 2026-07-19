"""多 Agent 桌面 UI 公共包。

包初始化保持轻量，避免 Agent UI 导入共享组件时反向加载主窗口造成循环导入。
请从 ``ui.main_ui`` 或兼容路径 ``ui.desktop`` 导入启动函数。
"""
