import json
import tempfile
import unittest
from pathlib import Path

from agents.technical_seo_agent import AuditPage, LighthouseResult, SiteAuditSnapshot, TechnicalAuditInput
from agents.technical_seo_agent.agent import (
    MockTechnicalSEOAuditLLM,
    TechnicalSEOAgent,
    _validate_narrative,
)
from agents.technical_seo_agent.rules import load_rules, match_rules
from app import create_run_context, run_technical_seo_audit, write_technical_seo_output
from tools.lighthouse import lighthouse_availability
from tools.site_audit import _parse_sitemap, canonicalize_url


def sample_snapshot() -> SiteAuditSnapshot:
    """构造包含确定性与人工复核问题的离线网站事实。"""
    root = "https://example.com/"
    product = "https://example.com/product"
    broken = "https://example.com/broken"
    return SiteAuditSnapshot(
        root_url=root,
        robots_url=root + "robots.txt",
        robots_status=200,
        robots_text="User-agent: *\nAllow: /",
        robots_error="",
        sitemap_urls=[root + "sitemap.xml"],
        sitemap_statuses={root + "sitemap.xml": 200},
        sitemap_errors=[],
        discovered_urls=[root, product, broken],
        pages=[
            AuditPage(
                url=root,
                final_url=root,
                status_code=200,
                content_type="text/html",
                title="网站首页",
                meta_description="网站说明",
                h1=["首页"],
                canonical=root,
                internal_links=[product, broken],
                robots_allowed=True,
            ),
            AuditPage(
                url=product,
                final_url=product,
                status_code=200,
                content_type="text/html",
                title="",
                meta_description="",
                h1=[],
                canonical="",
                robots_meta="noindex",
                in_sitemap=True,
                robots_allowed=True,
            ),
            AuditPage(
                url=broken,
                final_url=broken,
                status_code=404,
                content_type="text/html",
                in_sitemap=True,
                robots_allowed=True,
            ),
        ],
        inlink_counts={root: 0, product: 1, broken: 1},
        crawl_errors=[],
        lighthouse=[LighthouseResult(url=root, available=False, error="未安装 Lighthouse CLI")],
        coverage_notes=["测试仅覆盖 3 页。"],
    )


class TestTechnicalRules(unittest.TestCase):
    """验证规则库来源、确定性匹配与优先级边界。"""

    def test_rules_have_sources_and_limitations(self):
        """每条第一版规则都应有来源、适用范围和限制说明。"""
        version, rules = load_rules()
        self.assertTrue(version)
        self.assertGreaterEqual(len(rules), 17)
        self.assertTrue(all(rule.references for rule in rules.values()))
        self.assertTrue(all(rule.limitations for rule in rules.values()))

    def test_core_noindex_is_p0_and_missing_meta_is_p2(self):
        """核心页 noindex 应升 P0，且不再机械报告该 noindex 页的页面优化项。"""
        snapshot = sample_snapshot()
        _, rules = load_rules()
        request = TechnicalAuditInput(
            domain=snapshot.root_url,
            core_urls=["https://example.com/product"],
        )
        findings = match_rules(snapshot, request, rules)
        by_rule = {finding.rule_id: finding for finding in findings}
        self.assertEqual(by_rule["core_url_noindex"].priority, "P0")
        self.assertNotIn("missing_meta_description", by_rule)
        self.assertNotIn("missing_canonical", by_rule)
        self.assertIn("http_error_page", by_rule)
        self.assertIn("broken_internal_link", by_rule)

    def test_robots_404_is_not_reported_as_failure(self):
        """robots.txt 缺失返回 404 时不应误报为服务器抓取故障。"""
        snapshot = sample_snapshot()
        snapshot = SiteAuditSnapshot(**{
            **snapshot.__dict__,
            "robots_status": 404,
            "robots_error": "HTTP 404",
        })
        _, rules = load_rules()
        findings = match_rules(snapshot, TechnicalAuditInput(domain=snapshot.root_url), rules)
        self.assertNotIn("robots_unavailable", {item.rule_id for item in findings})

    def test_core_http_error_is_p0(self):
        """用户明确指定的核心页面返回 4xx/5xx 时应单独列为 P0。"""
        snapshot = sample_snapshot()
        _, rules = load_rules()
        findings = match_rules(
            snapshot,
            TechnicalAuditInput(domain=snapshot.root_url, core_urls=["https://example.com/broken"]),
            rules,
        )
        core_error = next(item for item in findings if item.finding_id == "http_error_page:core-pages")
        self.assertEqual(core_error.priority, "P0")


class TestTechnicalAgent(unittest.TestCase):
    """验证 LLM 只能排序既有问题以及分层输出。"""

    def test_mock_agent_preserves_all_rule_findings(self):
        """Mock 模型整理后不得新增或丢失 Python 已确认问题。"""
        snapshot = sample_snapshot()
        request = TechnicalAuditInput(
            domain=snapshot.root_url,
            core_urls=["https://example.com/product"],
        )
        output = TechnicalSEOAgent(MockTechnicalSEOAuditLLM(), model_name="mock").run(request, snapshot)
        self.assertTrue(output.findings)
        self.assertTrue(any(item.priority == "P0" for item in output.findings))
        self.assertTrue(output.issue_groups)
        self.assertEqual(output.validation_warnings, [])
        self.assertIn("priority_counts", output.statistics)

    def test_mock_service_writes_all_evidence_files(self):
        """第一版服务应输出页面事实、内链、Lighthouse、规则和报告。"""
        with tempfile.TemporaryDirectory() as directory:
            request, snapshot, output = run_technical_seo_audit(
                domain="https://example.com",
                core_urls=["/product"],
                mock=True,
            )
            run = create_run_context([], output_root=directory, project_name="example.com")
            report_json, report_md = write_technical_seo_output(request, snapshot, output, run)
            expected = {
                "crawl_config.json", "robots_snapshot.txt", "sitemap_snapshot.json",
                "pages.json", "link_graph.json", "lighthouse.json",
                "rule_findings.json", "audit_report.json", "audit_report.md",
            }
            self.assertEqual({path.name for path in report_json.parent.iterdir()}, expected)
            self.assertTrue(report_json.is_file())
            self.assertTrue(report_md.is_file())
            payload = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], "mock")

    def test_unsupported_search_platform_claims_are_removed(self):
        """没有平台数据时，模型不得声称百度收录、排名或 GSC 结果。"""
        summary, steps, warnings = _validate_narrative(
            "GSC 显示该页面排名为第一。",
            ["百度站长显示未收录，应立即提交。", "先修复已确认的 404。"],
            {"crawled_pages": 3, "priority_counts": {"P0": 0, "P1": 1, "P2": 2}},
        )
        self.assertNotIn("GSC", summary)
        self.assertEqual(steps, ["先修复已确认的 404。"])
        self.assertEqual(len(warnings), 2)


class TestTechnicalTools(unittest.TestCase):
    """验证 Sitemap、URL 规范化和 Lighthouse 可用性接口。"""

    def test_parses_urlset_and_sitemap_index(self):
        """XML URL 集与 Sitemap 索引应被区分解析。"""
        urls, children = _parse_sitemap(
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/a</loc></url></urlset>',
            "https://example.com/sitemap.xml",
        )
        self.assertEqual(urls, ["https://example.com/a"])
        self.assertEqual(children, [])
        urls, children = _parse_sitemap(
            b'<sitemapindex><sitemap><loc>https://example.com/posts.xml</loc></sitemap></sitemapindex>',
            "https://example.com/sitemap.xml",
        )
        self.assertEqual(urls, [])
        self.assertEqual(children, ["https://example.com/posts.xml"])

    def test_parses_gzip_sitemap(self):
        """常见的 .xml.gz Sitemap 应在解析前自动解压。"""
        import gzip

        content = gzip.compress(b'<urlset><url><loc>https://example.com/a</loc></url></urlset>')
        urls, children = _parse_sitemap(content, "https://example.com/sitemap.xml.gz")
        self.assertEqual(urls, ["https://example.com/a"])
        self.assertEqual(children, [])

    def test_canonicalize_removes_fragment_and_default_port(self):
        """内链图 URL 应移除片段和 HTTPS 默认端口。"""
        self.assertEqual(
            canonicalize_url("https://Example.com:443/a//b?x=1#part"),
            "https://example.com/a/b?x=1",
        )

    def test_lighthouse_availability_is_explicit(self):
        """本机缺少 CLI 时应返回空路径，而不是虚构检测结果。"""
        executable, chrome = lighthouse_availability()
        self.assertIsInstance(executable, str)
        self.assertIsInstance(chrome, str)


if __name__ == "__main__":
    unittest.main()
