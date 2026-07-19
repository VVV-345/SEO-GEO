"""Content Brief Agent 的独立 UI 占位模块。"""
from ui.widgets import PlaceholderView


class ContentBriefAgentView(PlaceholderView):
    """等待 Content Brief 业务实现的独立标签页。"""

    title = "Content Brief"

    def __init__(self, parent, **_dependencies) -> None:
        """创建 Content Brief 占位页面。"""
        super().__init__(parent, name=self.title)

