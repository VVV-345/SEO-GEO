import threading
import unittest

from agents.keyword_agent.ui import KeywordAgentView
from agents.serp_competitor_agent.ui import SerpCompetitorAgentView
from agents.technical_seo_agent.ui import TechnicalSEOAgentView
from ui.app_state import AppState
from ui.main_ui import AGENT_VIEWS, SEOAgentUI
from ui.task_runner import TaskRunner


class ImmediateRoot:
    """测试用 Tk 根替身，立即执行 after 回调。"""

    def after(self, _delay: int, callback) -> None:
        """同步执行回调，避免单元测试依赖 Tcl/Tk 环境。"""
        callback()


class TestAppState(unittest.TestCase):
    """验证 Agent 之间只通过共享状态和事件通信。"""

    def test_serp_update_publishes_without_ui_reference(self):
        """SERP 更新应广播数据，不需要持有任何 Agent View。"""
        state = AppState()
        received = []
        state.subscribe("serp_results_updated", received.append)
        results = {"词A": object()}
        output = object()
        state.set_serp_results(results, output)
        self.assertEqual(state.serp_results, results)
        self.assertIs(state.keyword_output, output)
        self.assertEqual(received, [results])

    def test_listener_failure_is_isolated(self):
        """一个模块事件监听器失败不能阻止其他模块收到状态。"""
        state = AppState()
        received = []
        state.subscribe("event", lambda _: (_ for _ in ()).throw(RuntimeError("broken")))
        state.subscribe("event", received.append)
        state.publish("event", 3)
        self.assertEqual(received, [3])


class TestTaskRunner(unittest.TestCase):
    """验证后台任务器的结果、异常和忙碌状态。"""

    def test_success_callback_and_busy_lifecycle(self):
        """成功任务应广播忙碌起止并交付结果。"""
        runner = TaskRunner(ImmediateRoot())
        busy = []
        results = []
        done = threading.Event()
        runner.subscribe_busy(busy.append)
        runner.start(
            lambda _progress: 42,
            on_success=lambda value: (results.append(value), done.set()),
            on_error=lambda _error: done.set(),
        )
        self.assertTrue(done.wait(2))
        self.assertEqual(results, [42])
        self.assertEqual(busy, [True, False])

    def test_error_callback_receives_exception(self):
        """失败任务应把原异常交给模块错误回调。"""
        runner = TaskRunner(ImmediateRoot())
        errors = []
        done = threading.Event()

        def worker(_progress):
            """模拟后台业务失败。"""
            raise ValueError("failed")

        runner.start(
            worker,
            on_success=lambda _value: done.set(),
            on_error=lambda error: (errors.append(error), done.set()),
        )
        self.assertTrue(done.wait(2))
        self.assertIsInstance(errors[0], ValueError)


class TestMainUIRegistry(unittest.TestCase):
    """验证主窗口只注册独立 Agent UI 类。"""

    def test_main_registry_contains_agent_owned_views(self):
        """已实现模块应从各 Agent 文件夹导入自己的 View。"""
        self.assertIn(KeywordAgentView, AGENT_VIEWS)
        self.assertIn(SerpCompetitorAgentView, AGENT_VIEWS)
        self.assertIn(TechnicalSEOAgentView, AGENT_VIEWS)
        self.assertTrue(all(view.__module__.startswith("agents.") for view in AGENT_VIEWS))

    def test_desktop_compatibility_alias(self):
        """旧的 ui.desktop 导入路径应继续指向新主窗口。"""
        from ui.desktop import SEOAgentUI as LegacySEOAgentUI

        self.assertIs(LegacySEOAgentUI, SEOAgentUI)

    def test_agent_ui_modules_do_not_import_each_other(self):
        """各 Agent UI 源码不得直接依赖另一个 Agent 的 UI 模块。"""
        from pathlib import Path

        paths = [
            Path("agents/keyword_agent/ui.py"),
            Path("agents/serp_competitor_agent/ui.py"),
            Path("agents/technical_seo_agent/ui.py"),
        ]
        module_names = {
            "agents.keyword_agent.ui",
            "agents.serp_competitor_agent.ui",
            "agents.technical_seo_agent.ui",
        }
        for path in paths:
            source = path.read_text(encoding="utf-8")
            own = str(path.with_suffix("")).replace("\\", ".").replace("/", ".")
            for module in module_names - {own}:
                self.assertNotIn(f"from {module}", source)
                self.assertNotIn(f"import {module}", source)


if __name__ == "__main__":
    unittest.main()
