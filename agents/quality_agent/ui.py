"""SEO/GEO 质检 Agent 的独立 UI 占位模块。"""
from ui.widgets import PlaceholderView


class QualityAgentView(PlaceholderView):
    """等待质检业务实现的独立标签页。"""

    title = "SEO/GEO 质检"

    def __init__(self, parent, **_dependencies) -> None:
        """创建 SEO/GEO 质检占位页面。"""
        super().__init__(parent, name=self.title)

