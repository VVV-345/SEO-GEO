"""旧导入路径兼容层；实际主窗口位于 ``ui.main_ui``。"""

from .main_ui import SEOAgentUI, launch

__all__ = ["SEOAgentUI", "launch"]
