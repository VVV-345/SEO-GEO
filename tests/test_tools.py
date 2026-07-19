import tempfile
import unittest
from pathlib import Path

from tools.baidu_serp import (
    BAIDU_HEADERS,
    BaiduSERPClient,
    SearchResult,
    is_baidu_verification_page,
    parse_serp_html,
)
from tools.file_reader import read_document
from tools.webpage import DEFAULT_HEADERS, clean_html


class TestFileReader(unittest.TestCase):
    def test_reads_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "business.txt"
            path.write_text("企业知识库\n\n\n私有化部署", encoding="utf-8")
            document = read_document(path)
        self.assertEqual(document.kind, "txt")
        self.assertEqual(document.text, "企业知识库\n\n私有化部署")


class TestWebPage(unittest.TestCase):
    def test_extracts_metadata_headings_and_main_text(self):
        html = """<html><head><title>产品页</title><meta name="description" content="产品说明"></head>
        <body><nav>菜单</nav><main><h1>企业知识库</h1><h2>私有部署</h2><p>这里是有用的正文内容，介绍产品能力。</p></main><script>bad()</script></body></html>"""
        title, description, headings, text = clean_html(html)
        self.assertEqual(title, "产品页")
        self.assertEqual(description, "产品说明")
        self.assertEqual(headings, ["企业知识库", "私有部署"])
        self.assertIn("有用的正文", text)
        self.assertNotIn("bad()", text)


class TestBaiduParser(unittest.TestCase):
    def test_parses_related_and_results(self):
        html = """<div id="rs"><a>相关词一</a></div>
        <div class="result c-container" mu="https://example.com/a"><h3><a href="https://example.com/a">标题一</a></h3><div class="c-abstract">摘要一</div></div>"""
        related, results = parse_serp_html(html)
        self.assertEqual(related, ["相关词一"])
        self.assertEqual(results[0].domain, "example.com")
        self.assertEqual(results[0].snippet, "摘要一")

    def test_skips_result_without_landing_url(self):
        html = '<div class="result c-container"><h3><a>没有链接</a></h3></div>'
        _, results = parse_serp_html(html)
        self.assertEqual(results, [])

    def test_parses_mobile_result_data_url(self):
        html = '<div class="c-result" data-url="https://mobile.example.com/a"><h3><a>移动结果</a></h3></div>'
        _, results = parse_serp_html(html, base_url="https://m.baidu.com/s")
        self.assertEqual(results[0].url, "https://mobile.example.com/a")
        self.assertEqual(results[0].domain, "mobile.example.com")

    def test_detects_verification_page(self):
        self.assertTrue(is_baidu_verification_page("<title>百度安全验证</title>"))
        self.assertFalse(is_baidu_verification_page("<title>正常搜索结果</title>"))

    def test_parses_related_searches_from_recommend_block(self):
        html = '<div class="c-recomm-wrap"><span>大家还在搜</span><a>合肥AI企业</a><a>人工智能产业园</a></div>'
        related, _ = parse_serp_html(html)
        self.assertEqual(related, ["合肥AI企业", "人工智能产业园"])


class TestSERPHeaders(unittest.TestCase):
    def test_default_headers_look_like_a_real_browser(self):
        self.assertIn("Chrome/", DEFAULT_HEADERS["User-Agent"])
        for required in ("Accept", "Accept-Language", "Sec-Ch-Ua", "Sec-Ch-Ua-Platform"):
            self.assertIn(required, DEFAULT_HEADERS)

    def test_baidu_headers_add_navigation_context(self):
        self.assertIn("baidu.com", BAIDU_HEADERS["Referer"])
        self.assertEqual(BAIDU_HEADERS["Sec-Fetch-Site"], "same-origin")
        for key in DEFAULT_HEADERS:  # 百度头应继承全部通用浏览器头
            self.assertIn(key, BAIDU_HEADERS)


class TestSERPClientResolve(unittest.TestCase):
    def test_external_domains_are_resolved_unchanged(self):
        client = BaiduSERPClient(delay=0)
        original = SearchResult(1, "标题", "https://zhihu.com/question/1", "zhihu.com")
        # 外部域名不触发 /link 跳转，原样返回且不发请求
        self.assertIs(client._resolve_result(original), original)

    def test_finalize_caps_to_resolve_top_n(self):
        client = BaiduSERPClient(resolve_top_n=3, delay=0)
        items = [
            SearchResult(i, f"标题{i}", f"https://site{i}.cn/a", f"site{i}.cn")
            for i in range(1, 6)
        ]
        finalized = client._finalize_results(items)
        self.assertEqual(len(finalized), 3)
        self.assertEqual([item.url for item in finalized], [items[0].url, items[1].url, items[2].url])


if __name__ == "__main__":
    unittest.main()
