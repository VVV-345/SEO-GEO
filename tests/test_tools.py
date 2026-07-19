import tempfile
import unittest
from pathlib import Path

from tools.baidu_serp import (
    BAIDU_HEADERS,
    BaiduSERPClient,
    SearchResult,
    competitor_url_rejection_reason,
    is_baidu_verification_page,
    parse_serp_html,
)
from tools.file_reader import read_document
from tools.webpage import DEFAULT_HEADERS, clean_html, decode_html_bytes


class TestFileReader(unittest.TestCase):
    def test_reads_utf8_text(self):
        """文本资料应按 UTF-8 读取并压缩多余空行。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "business.txt"
            path.write_text("企业知识库\n\n\n私有化部署", encoding="utf-8")
            document = read_document(path)
        self.assertEqual(document.kind, "txt")
        self.assertEqual(document.text, "企业知识库\n\n私有化部署")


class TestWebPage(unittest.TestCase):
    def test_extracts_metadata_headings_and_main_text(self):
        """网页清洗应保留 SEO 元信息与正文，并移除脚本噪声。"""
        html = """<html><head><title>产品页</title><meta name="description" content="产品说明"></head>
        <body><nav>菜单</nav><main><h1>企业知识库</h1><h2>私有部署</h2><p>这里是有用的正文内容，介绍产品能力。</p></main><script>bad()</script></body></html>"""
        title, description, headings, text = clean_html(html)
        self.assertEqual(title, "产品页")
        self.assertEqual(description, "产品说明")
        self.assertEqual(headings, ["企业知识库", "私有部署"])
        self.assertIn("有用的正文", text)
        self.assertNotIn("bad()", text)

    def test_decodes_utf8_chinese_without_declared_charset(self):
        """未声明 charset 的 UTF-8 中文不能被 requests 默认编码解成乱码。"""
        html = "<title>大模型智能问答</title><p>企业知识库解决方案</p>"
        decoded = decode_html_bytes(
            html.encode("utf-8"),
            content_type="text/html",
            apparent_encoding="ISO-8859-1",
        )
        self.assertIn("大模型智能问答", decoded)
        self.assertNotIn("å¤§", decoded)

    def test_decodes_gb18030_from_meta_charset(self):
        """中文旧站在 meta 声明 GB 编码时仍应正确解码。"""
        html = '<meta charset="gb2312"><title>企业知识库</title>'
        decoded = decode_html_bytes(html.encode("gb18030"), content_type="text/html")
        self.assertIn("企业知识库", decoded)


class TestBaiduParser(unittest.TestCase):
    def test_parses_related_and_results(self):
        """静态百度 HTML 应解析相关词、落地页、域名和摘要。"""
        html = """<div id="rs"><a>相关词一</a></div>
        <div class="result c-container" mu="https://example.com/a"><h3><a href="https://example.com/a">标题一</a></h3><div class="c-abstract">摘要一</div></div>"""
        related, results = parse_serp_html(html)
        self.assertEqual(related, ["相关词一"])
        self.assertEqual(results[0].domain, "example.com")
        self.assertEqual(results[0].snippet, "摘要一")

    def test_skips_result_without_landing_url(self):
        """没有落地地址的聚合块不能进入后续竞品分析。"""
        html = '<div class="result c-container"><h3><a>没有链接</a></h3></div>'
        _, results = parse_serp_html(html)
        self.assertEqual(results, [])

    def test_parses_mobile_result_data_url(self):
        """移动模板的 data-url 应优先作为真实落地页。"""
        html = '<div class="c-result" data-url="https://mobile.example.com/a"><h3><a>移动结果</a></h3></div>'
        _, results = parse_serp_html(html, base_url="https://m.baidu.com/s")
        self.assertEqual(results[0].url, "https://mobile.example.com/a")
        self.assertEqual(results[0].domain, "mobile.example.com")

    def test_detects_verification_page(self):
        """安全验证页应被识别，避免误报为正常空结果。"""
        self.assertTrue(is_baidu_verification_page("<title>百度安全验证</title>"))
        self.assertFalse(is_baidu_verification_page("<title>正常搜索结果</title>"))

    def test_parses_related_searches_from_recommend_block(self):
        """新版推荐容器中的相关搜索词应被提取。"""
        html = '<div class="c-recomm-wrap"><span>大家还在搜</span><a>合肥AI企业</a><a>人工智能产业园</a></div>'
        related, _ = parse_serp_html(html)
        self.assertEqual(related, ["合肥AI企业", "人工智能产业园"])


class TestSERPHeaders(unittest.TestCase):
    def test_default_headers_look_like_a_real_browser(self):
        """通用请求头应包含一致的浏览器识别字段。"""
        self.assertIn("Chrome/", DEFAULT_HEADERS["User-Agent"])
        for required in ("Accept", "Accept-Language", "Sec-Ch-Ua", "Sec-Ch-Ua-Platform"):
            self.assertIn(required, DEFAULT_HEADERS)

    def test_baidu_headers_add_navigation_context(self):
        """百度专用请求头应继承通用头并补充导航上下文。"""
        self.assertIn("baidu.com", BAIDU_HEADERS["Referer"])
        self.assertEqual(BAIDU_HEADERS["Sec-Fetch-Site"], "same-origin")
        for key in DEFAULT_HEADERS:  # 百度头应继承全部通用浏览器头
            self.assertIn(key, BAIDU_HEADERS)


class TestSERPClientResolve(unittest.TestCase):
    def test_external_domains_are_resolved_unchanged(self):
        """已经是站外落地页的结果不得再次请求解析。"""
        client = BaiduSERPClient(delay=0)
        original = SearchResult(1, "标题", "https://zhihu.com/question/1", "zhihu.com")
        # 外部域名不触发 /link 跳转，原样返回且不发请求
        self.assertIs(client._resolve_result(original), original)

    def test_finalize_caps_to_resolve_top_n(self):
        """URL 解析数量应受配置上限控制。"""
        client = BaiduSERPClient(resolve_top_n=3, delay=0)
        items = [
            SearchResult(i, f"标题{i}", f"https://site{i}.cn/a", f"site{i}.cn")
            for i in range(1, 6)
        ]
        finalized, filtered = client._finalize_results(items)
        self.assertEqual(len(finalized), 3)
        self.assertEqual([item.url for item in finalized], [items[0].url, items[1].url, items[2].url])
        self.assertEqual(filtered, [])

    def test_filters_non_content_serp_urls_and_keeps_reasons(self):
        """明显的百度中转、广告、搜索聚合与付费地址不得进入竞品 URL。"""
        client = BaiduSERPClient(resolve_top_n=10, delay=0)
        items = [
            SearchResult(1, "中转", "https://www.baidu.com/other.php?url=x", "baidu.com"),
            SearchResult(2, "广告", "https://ada.baidu.com/site/x/agent", "ada.baidu.com"),
            SearchResult(3, "聚合", "https://wenku.baidu.com/search?word=x", "wenku.baidu.com"),
            SearchResult(4, "付费", "https://vendor.cn/product?utm_medium=cpc", "vendor.cn"),
            SearchResult(5, "正文", "https://vendor.cn/article", "vendor.cn"),
        ]
        client._resolve_result = lambda item: item
        finalized, filtered = client._finalize_results(items)
        self.assertEqual([item.url for item in finalized], ["https://vendor.cn/article"])
        self.assertEqual(len(filtered), 4)
        self.assertTrue(all(item.reason for item in filtered))

    def test_does_not_filter_normal_baidu_content_page(self):
        """百度百科等独立内容页即使可能 403，也不应在 URL 阶段武断删除。"""
        reason = competitor_url_rejection_reason("https://baike.baidu.com/item/AI%E7%9F%A5%E8%AF%86%E5%BA%93/1")
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
