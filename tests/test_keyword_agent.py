import unittest

from seo_agents.keyword_agent import (
    Candidate,
    assign_tier,
    composite_score,
    dedupe_candidates,
    filter_by_relevance,
    normalize_keyword,
    run_keyword_agent,
)


class _FakeLLM:
    """按调用名返回写死响应的假客户端。"""

    def __init__(self):
        self.calls: list[str] = []

    def chat_json(self, system, user, *, name="call", temperature=0.5):
        self.calls.append(name)
        if name == "expand":
            return {"candidates": [
                {"keyword": "企业知识库私有化部署", "intent": "commercial", "relevance": "high", "note": "a"},
                {"keyword": " 企业知识库 私有化部署。 ", "intent": "commercial", "relevance": "high", "note": "a 的近重复"},
                {"keyword": "免费个人笔记软件", "intent": "commercial", "relevance": "low", "note": "客户不做 C 端"},
                {"keyword": "知识库是什么", "intent": "informational", "relevance": "medium", "note": "b"},
            ]}
        # rank 收到的是过滤去重后的 2 个：私有化部署、知识库是什么
        return {"ranked": [
            {"keyword": "企业知识库私有化部署", "intent": "commercial", "commercial_intent": 5, "specificity": 4, "serp_difficulty": 3, "rationale": "r1"},
            {"keyword": "知识库是什么", "intent": "informational", "commercial_intent": 2, "specificity": 2, "serp_difficulty": 4, "rationale": "r2"},
        ]}


class TestNormalize(unittest.TestCase):
    def test_strips_outer_whitespace_and_trailing_punct(self):
        self.assertEqual(normalize_keyword(" 企业知识库私有化部署。 "), "企业知识库私有化部署")

    def test_collapses_inner_spaces(self):
        self.assertEqual(normalize_keyword("a   b\tc"), "a b c")

    def test_strips_leading_punct(self):
        self.assertEqual(normalize_keyword("？企业知识库"), "企业知识库")


class TestDedupe(unittest.TestCase):
    def test_merges_near_duplicates_by_compact_form(self):
        cs = [
            Candidate("企业知识库私有化部署", "commercial", "high", ""),
            Candidate("企业知识库 私有化部署。", "commercial", "high", ""),  # 仅多空格+句号 → 合并
            Candidate("知识库是什么", "informational", "medium", ""),
        ]
        out = dedupe_candidates(cs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].keyword, "企业知识库私有化部署")

    def test_keeps_distinct(self):
        cs = [Candidate("a", "commercial", "high", ""), Candidate("b", "commercial", "high", "")]
        self.assertEqual(len(dedupe_candidates(cs)), 2)


class TestFilter(unittest.TestCase):
    def test_drops_low_relevance(self):
        cs = [
            Candidate("x", "commercial", "high", ""),
            Candidate("y", "commercial", "low", ""),
            Candidate("z", "informational", "medium", ""),
        ]
        out = filter_by_relevance(cs)
        self.assertEqual([c.keyword for c in out], ["x", "z"])


class TestScoring(unittest.TestCase):
    def test_composite_formula(self):
        self.assertEqual(composite_score(5, 4, 3), 12)
        self.assertEqual(composite_score(2, 2, 4), 6)
        self.assertEqual(composite_score(1, 1, 5), 3)   # 下限
        self.assertEqual(composite_score(5, 5, 1), 15)  # 上限

    def test_tier_thresholds(self):
        self.assertEqual(assign_tier(12, "high"), "P1")
        self.assertEqual(assign_tier(11, "high"), "P1")
        self.assertEqual(assign_tier(10, "high"), "P2")
        self.assertEqual(assign_tier(8, "high"), "P2")
        self.assertEqual(assign_tier(7, "high"), "P3")
        self.assertEqual(assign_tier(15, "low"), "P3")  # 低相关性强制 P3


class TestPipeline(unittest.TestCase):
    def test_end_to_end_filters_dedupes_and_ranks(self):
        fake = _FakeLLM()
        result = run_keyword_agent(
            seeds=["企业知识库"],
            business_text="客户做企业知识库私有化部署。",
            existing_pages=[],
            llm=fake,
            num=50,
            model_name="fake",
        )
        self.assertEqual(fake.calls, ["expand", "rank"])
        self.assertEqual(result.candidates_raw, 4)
        self.assertEqual(result.candidates_after_filter, 2)  # 丢 low + 合并近重复
        self.assertEqual(len(result.items), 2)

        tiers = {it.keyword: it.tier for it in result.items}
        self.assertEqual(tiers["企业知识库私有化部署"], "P1")  # composite=12
        self.assertEqual(tiers["知识库是什么"], "P3")          # composite=6
        self.assertEqual(result.items[0].tier, "P1")          # P1 排在 P3 前


if __name__ == "__main__":
    unittest.main()
