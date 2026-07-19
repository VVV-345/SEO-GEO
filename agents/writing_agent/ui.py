"""写作 Agent 的独立 UI 占位模块。"""
from ui.widgets import PlaceholderView


class WritingAgentView(PlaceholderView):
    """等待写作业务实现的独立标签页。"""

    title = "写作"

    def __init__(self, parent, **_dependencies) -> None:
        """创建写作 Agent 占位页面。"""
        super().__init__(parent, name=self.title)

