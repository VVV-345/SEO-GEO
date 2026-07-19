import unittest

from app import (
    build_selected_keyword_output,
    fetch_keyword_serp,
    generate_keyword_candidates,
    render_candidate_report,
    render_keyword_report,
)
from agents.keyword_agent import KeywordAgent, KeywordAgentInput, MockKeywordLLM
from agents.keyword_agent.scoring import estimate_competition, opportunity_score, priority
from tools.baidu_serp import BaiduSERP, SearchResult
from tools.progress import ProgressReporter


class FakeSERPClient:
    def search(self, keyword: str, *, limit: int = 10) -> BaiduSERP:
        results = [
            SearchResult(1, f"{keyword}完整指南", "https://example.com/guide", "example.com"),
            SearchResult(2, "经验分享", "https://zhihu.com/question/1", "zhihu.com"),
            SearchResult(3, "行业实践", "https://vendor-a.cn/case", "vendor-a.cn"),
            SearchResult(4, "解决方案", "https://vendor-b.cn/solution", "vendor-b.cn"),
            SearchResult(5, "产品介绍", "https://vendor-c.cn/product", "vendor-c.cn"),
        ]
        return BaiduSERP(keyword, [keyword + " 价格"], [keyword + " 怎么做"], results, True)


class SuggestionFailureSERPClient:
    """用于说明 Agent 可接受部分 SERP；具体 HTTP 容错由工具层负责。"""

    def search(self, keyword: str, *, limit: int = 10) -> BaiduSERP:
        return BaiduSERP(keyword=keyword, error="下拉词获取失败", complete=False)


class CapturingLLM(MockKeywordLLM):
    def __init__(self):
        self.expand_user = ""

    def chat_json(self, system, user, *, name="call", temperature=0.3):
        if name == "expand_keywords":
            self.expand_user = user
        return super().chat_json(system, user, name=name, temperature=temperature)


class TestCompetitionRules(unittest.TestCase):
    def test_unknown_when_no_results(self):
        evidence = estimate_competition("企业知识库", [])
        self.assertEqual(evidence.level, "unknown")
        self.assertEqual(evidence.score, 50)

    def test_rule_uses_visible_serp_features(self):
        results = [
            SearchResult(1, "企业知识库部署", "https://zhihu.com/question/1", "zhihu.com"),
            SearchResult(2, "企业知识库方案", "https://baike.baidu.com/item/a", "baike.baidu.com"),
            SearchResult(3, "企业知识库", "https://example.com", "example.com"),
            SearchResult(4, "知识管理", "https://other.cn/a", "other.cn"),
            SearchResult(5, "企业知识库指南", "https://third.cn/a", "third.cn"),
        ]
        evidence = estimate_competition("企业知识库", results)
        self.assertEqual(evidence.exact_title_ratio, 0.8)
        self.assertEqual(evidence.authority_ratio, 0.4)
        self.assertEqual(evidence.homepage_ratio, 0.2)
        self.assertEqual(evidence.level, "medium")

    def test_priority_needs_business_fit(self):
        competition = estimate_competition("x", [
            SearchResult(i, "弱相关", f"https://site{i}.cn/a", f"site{i}.cn") for i in range(1, 6)
        ])
        score = opportunity_score(5, 5, 5, competition)
        self.assertEqual(priority(score, 5, competition.level), "P1")
        self.assertNotEqual(priority(score, 2, competition.level), "P1")


class TestKeywordAgent(unittest.TestCase):
    def test_end_to_end_with_injected_serp(self):
        progress = ProgressReporter()
        output = KeywordAgent(MockKeywordLLM(), FakeSERPClient(), model_name="mock").run(
            KeywordAgentInput(
                seeds=["企业知识库"],
                requirement="重点研究制造业，排除个人知识库",
                business_text="客户提供私有化企业知识库。",
                candidate_limit=10,
            ),
            progress=progress,
        )
        self.assertEqual(len(output.opportunities), 3)
        self.assertTrue(all(item.serp_complete for item in output.opportunities))
        self.assertTrue(output.opportunities[0].suggestions)
        self.assertTrue(output.opportunities[0].top_urls)
        self.assertEqual(
            [event.stage for event in progress.events if event.status == "started"],
            ["keyword.expand", "keyword.serp", "keyword.rank"],
        )
        serp_events = [event for event in progress.events if event.stage == "keyword.serp" and event.status == "running"]
        self.assertEqual((serp_events[-1].current, serp_events[-1].total), (3, 3))
        report = render_keyword_report(output)
        self.assertIn("P1", report)
        self.assertIn("SERP + ", report)
        self.assertNotIn('"opportunities"', report)
        self.assertIn("业务评分", report)
        self.assertIn("百度下拉词", report)
        self.assertIn("百度相关搜索", report)
        self.assertIn("SERP 前列 URL", report)
        self.assertIn("https://example.com/guide", report)
        self.assertIn("拓展词：", report)
        self.assertIn("=" * 72, report)
        self.assertEqual(output.requirement, "重点研究制造业，排除个人知识库")

    def test_requirement_is_sent_to_expand_llm(self):
        llm = CapturingLLM()
        KeywordAgent(llm, FakeSERPClient(), model_name="mock").run(
            KeywordAgentInput(seeds=["企业知识库"], requirement="只研究政企采购场景")
        )
        self.assertIn("只研究政企采购场景", llm.expand_user)

    def test_two_stage_mock_only_builds_selected_keywords(self):
        candidates = generate_keyword_candidates(
            seeds=["企业知识库"],
            requirement="只研究企业采购",
            candidate_limit=10,
            mock=True,
        )
        self.assertEqual(len(candidates.candidates), 3)
        preview = render_candidate_report(candidates)
        self.assertIn("尚未查询", preview)
        selected = [candidates.candidates[0].keyword]
        serp = fetch_keyword_serp(selected, mock=True)
        self.assertEqual(set(serp), set(selected))
        final = build_selected_keyword_output(candidates, selected, serp, mock=True)
        self.assertEqual([item.keyword for item in final.opportunities], selected)

    def test_partial_serp_does_not_abort_agent(self):
        output = KeywordAgent(MockKeywordLLM(), SuggestionFailureSERPClient(), model_name="mock").run(
            KeywordAgentInput(["企业知识库"], candidate_limit=10)
        )
        self.assertEqual(len(output.opportunities), 3)
        self.assertTrue(all(item.competition.level == "unknown" for item in output.opportunities))
        self.assertTrue(all(item.priority == "待验证" for item in output.opportunities))


if __name__ == "__main__":
    unittest.main()
