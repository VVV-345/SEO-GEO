import unittest

from tools.progress import ProgressReporter


class TestProgressReporter(unittest.TestCase):
    def test_records_events_and_calculates_percent(self):
        """事件应被保存、广播，并根据当前数量计算百分比。"""
        received = []
        reporter = ProgressReporter([received.append])
        event = reporter.step("keyword.serp", "查询百度 SERP", "正在查询", current=2, total=5)
        self.assertEqual(event.percent, 40)
        self.assertEqual(reporter.events, received)

    def test_listener_error_does_not_break_progress(self):
        """一个 UI 监听器异常不能破坏业务进度事件。"""
        reporter = ProgressReporter([lambda _: (_ for _ in ()).throw(RuntimeError("UI failed"))])
        event = reporter.completed("keyword.rank", "排序关键词机会", "完成", total=3)
        self.assertEqual(event.status, "completed")
        self.assertEqual(event.percent, 100)


if __name__ == "__main__":
    unittest.main()
