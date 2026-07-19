import json
import tempfile
import unittest
from pathlib import Path

from agents.serp_competitor_agent import CompetitorAnalysisInput, SerpCompetitorAgent
from app import create_run_context, write_competitor_output
from tools.webpage import WebPageContent, clean_html_details


class FixedAnalysisLLM:
    """记录模型输入并返回确定结果，确保测试不依赖外部 API。"""

    def __init__(self) -> None:
        """初始化最近一次用户消息，供断言页面证据是否完整传递。"""
        self.last_user = ""

    def chat_json(self, system, user, *, name="call", temperature=0.3):
        """返回合法竞品报告，并保存 Agent 发送的结构化证据。"""
        del system, name, temperature
        self.last_user = user
        return {
            "search_intent": "企业采购前的私有化部署评估",
            "page_type_summary": ["解决方案页"],
            "common_topics": ["部署架构"],
            "common_sections": ["实施步骤"],
            "common_faqs": ["部署需要多久"],
            "case_evidence": ["某制造客户部署案例"],
            "data_evidence": ["页面声称部署周期为2周"],
            "content_gaps": ["迁移清单需要客户确认"],
            "must_cover": ["安全与成本"],
            "recommended_structure": ["H1：企业知识库私有化部署"],
            "evidence_notes": ["基于一页离线快照"],
        }


def fake_fetcher(url: str, *, max_chars: int = 12_000) -> WebPageContent:
    """为正常 URL 返回结构化页面，为 fail URL 模拟单页抓取失败。"""
    if "fail" in url:
        raise RuntimeError("页面禁止访问")
    return WebPageContent(
        requested_url=url,
        final_url=url,
        status_code=200,
        title="私有化部署方案",
        description="方案说明",
        headings=["企业知识库", "实施步骤"],
        heading_structure=[{"level": 1, "text": "企业知识库"}],
        faq_questions=["部署需要多久？"],
        tables=["阶段 | 周期\n部署 | 2周"],
        case_mentions=["某制造客户完成部署"],
        data_points=["部署周期为2周"],
        text="页面正文"[:max_chars],
    )


class TestPageEvidence(unittest.TestCase):
    """验证网页工具能提取竞品分析所需的关键结构。"""

    def test_extracts_heading_levels_faq_schema_and_tables(self):
        """应同时保留标题层级、FAQ JSON-LD 和表格文本。"""
        html = """<html><head><title>方案</title>
        <script type="application/ld+json">{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"费用是多少？"}]}</script>
        </head><body><h1>产品方案</h1><h2>部署步骤</h2>
        <table><tr><th>项目</th><th>周期</th></tr><tr><td>上线</td><td>2周</td></tr></table>
        <p>某制造客户完成部署，周期为2周。</p></body></html>"""
        details = clean_html_details(html)
        self.assertEqual(details["heading_structure"][1], {"level": 2, "text": "部署步骤"})
        self.assertIn("费用是多少？", details["faq_questions"])
        self.assertIn("项目 | 周期", details["tables"][0])
        self.assertIn("某制造客户完成部署，周期为2周", details["case_mentions"])
        self.assertIn("某制造客户完成部署，周期为2周", details["data_points"])


class TestSerpCompetitorAgent(unittest.TestCase):
    """验证竞品 Agent 的容错、证据传递和输出隔离。"""

    def test_failed_page_does_not_abort_and_evidence_reaches_llm(self):
        """单页失败应被记录，成功页的 FAQ/表格仍应进入模型输入。"""
        llm = FixedAnalysisLLM()
        agent = SerpCompetitorAgent(llm, model_name="test", page_fetcher=fake_fetcher)
        output = agent.run(CompetitorAnalysisInput(
            keyword="企业知识库私有化部署",
            urls=["https://example.com/good", "https://example.com/fail"],
        ))
        payload = json.loads(llm.last_user)
        self.assertEqual(output.search_intent, "企业采购前的私有化部署评估")
        self.assertEqual(len(output.pages), 2)
        self.assertIn("页面禁止访问", output.pages[1].error)
        self.assertEqual(payload["pages"][0]["faq_questions"], ["部署需要多久？"])
        self.assertTrue(payload["pages"][0]["tables"])
        self.assertTrue(payload["pages"][0]["case_mentions"])
        self.assertTrue(payload["pages"][0]["data_points"])

    def test_outputs_are_saved_under_keyword_directory(self):
        """同一运行的不同关键词应写入各自目录，避免报告覆盖。"""
        llm = FixedAnalysisLLM()
        agent = SerpCompetitorAgent(llm, model_name="test", page_fetcher=fake_fetcher)
        output = agent.run(CompetitorAnalysisInput(keyword="私有化/部署", urls=["https://example.com/good"]))
        with tempfile.TemporaryDirectory() as directory:
            run = create_run_context(["企业知识库"], output_root=directory)
            report_json, report_md = write_competitor_output(output, run)
            self.assertEqual(report_json.parent.name, "私有化_部署")
            self.assertTrue(report_json.is_file())
            self.assertTrue(report_md.is_file())
            self.assertTrue((Path(report_json.parent) / "pages.json").is_file())


if __name__ == "__main__":
    unittest.main()
