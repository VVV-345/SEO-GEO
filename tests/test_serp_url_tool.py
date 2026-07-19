import unittest

from tools.baidu_serp import BaiduSERP, SearchResult
from tools.serp_url_tool import SerpURLTool


class FakeClient:
    def __init__(self):
        self.queries = []

    def search(self, keyword, *, limit=10):
        self.queries.append(keyword)
        return BaiduSERP(
            keyword=keyword,
            results=[SearchResult(1, keyword, f"https://example.com/{len(self.queries)}", "example.com")],
            complete=True,
        )

    def close(self):
        pass


class TestSerpURLTool(unittest.TestCase):
    def test_fetch_many_queries_only_selected_keywords(self):
        tool = SerpURLTool.__new__(SerpURLTool)
        tool.client = FakeClient()
        selected = ["词A", "词C"]
        results = tool.fetch_many(selected)
        self.assertEqual(tool.client.queries, selected)
        self.assertEqual(set(results), set(selected))

    def test_single_retry_queries_only_one_keyword(self):
        tool = SerpURLTool.__new__(SerpURLTool)
        tool.client = FakeClient()
        result = tool.fetch("失败词")
        self.assertEqual(tool.client.queries, ["失败词"])
        self.assertTrue(result.results)


if __name__ == "__main__":
    unittest.main()
