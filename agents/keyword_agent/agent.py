from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from core.llm import JSONLLM
from tools.baidu_serp import BaiduSERP, BaiduSERPClient
from tools.progress import ProgressReporter

from .models import CandidateKeyword, KeywordAgentInput, KeywordAgentOutput, KeywordOpportunity
from .prompts import EXPAND_SYSTEM, RANK_SYSTEM
from .scoring import estimate_competition, opportunity_score, priority


def _clamp(value: Any) -> int:
    """模型偶尔会输出字符串或越界数值；统一收敛为评分协议的 1-5。"""
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def _key(keyword: str) -> str:
    """用于匹配模型往返结果，不改变最终展示给用户的原始关键词。"""
    return "".join(keyword.lower().split()).strip("，,。！？?!；;：:")


def _valid_url(url: str) -> bool:
    """只保留可交给后续竞品 Agent 的公开 HTTP(S) 落地页。"""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_candidates(data: dict[str, Any], limit: int) -> list[CandidateKeyword]:
    """容错解析模型 JSON，并仅做字面归一去重。

    语义聚类由扩词提示词完成；这里不擅自合并不同的搜索任务。
    """
    candidates: list[CandidateKeyword] = []
    seen: set[str] = set()
    for raw in data.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        keyword = str(raw.get("keyword", "")).strip()
        key = _key(keyword)
        if not key or key in seen:
            continue
        seen.add(key)
        variants = [str(value).strip() for value in raw.get("variants", []) if str(value).strip()]
        candidates.append(CandidateKeyword(
            keyword=keyword,
            variants=variants,
            intent=str(raw.get("intent", "informational")).strip().lower(),
            business_fit=_clamp(raw.get("business_fit")),
            commercial_proximity=_clamp(raw.get("commercial_proximity")),
            specificity=_clamp(raw.get("specificity")),
            rationale=str(raw.get("rationale", "")).strip(),
        ))
        if len(candidates) >= limit:
            break
    return candidates


class KeywordAgent:
    """关键词机会流程：LLM 扩词 → 百度快照 → 确定性规则打分 → LLM 证据排序。"""

    def __init__(self, llm: JSONLLM, serp_client: BaiduSERPClient | None = None, *, model_name: str = "unknown"):
        self.llm = llm
        self.serp = serp_client or BaiduSERPClient()
        self.model_name = model_name

    def run(
        self,
        request: KeywordAgentInput,
        *,
        source_files: list[str] | None = None,
        existing_pages: list[dict[str, str]] | None = None,
        progress: ProgressReporter | None = None,
    ) -> KeywordAgentOutput:
        progress = progress or ProgressReporter()
        pages = existing_pages or []
        progress.started("keyword.expand", "扩展候选词", "LLM 正在扩展、聚类并标注意图")
        expanded = self.llm.chat_json(
            EXPAND_SYSTEM,
            json.dumps({
                "seeds": request.seeds,
                "requirement": request.requirement or "（未提供）",
                "candidate_limit": request.candidate_limit,
                "business_material": request.business_text or "（未提供）",
                "existing_pages": pages,
            }, ensure_ascii=False),
            name="expand_keywords",
            temperature=0.6,
        )
        candidates = _parse_candidates(expanded, request.candidate_limit)
        if not candidates:
            progress.failed("keyword.expand", "扩展候选词", "LLM 没有返回可用的候选关键词")
            raise RuntimeError("LLM 没有返回可用的候选关键词。")
        progress.completed("keyword.expand", "扩展候选词", f"已得到 {len(candidates)} 个去重候选词", total=len(candidates))

        # SERP 逐词采集，避免把模型生成的词与真实搜索证据混为一谈。
        rows: list[tuple[CandidateKeyword, BaiduSERP, Any, int]] = []
        progress.started("keyword.serp", "查询百度 SERP", "开始查询候选词的百度结果", total=len(candidates))
        for index, candidate in enumerate(candidates, 1):
            progress.step(
                "keyword.serp", "查询百度 SERP", f"正在查询：{candidate.keyword}",
                current=index - 1, total=len(candidates),
            )
            serp = self.serp.search(candidate.keyword, limit=request.serp_limit)
            competition = estimate_competition(candidate.keyword, serp.results)
            score = opportunity_score(
                candidate.business_fit, candidate.commercial_proximity, candidate.specificity, competition
            )
            rows.append((candidate, serp, competition, score))
            if serp.error == "Mock 模式未访问百度":
                state = "Mock 跳过百度查询"
            else:
                state = "完成" if serp.complete else "结果不完整"
            progress.step(
                "keyword.serp", "查询百度 SERP", f"{state}：{candidate.keyword}",
                current=index, total=len(candidates),
            )
        progress.completed("keyword.serp", "查询百度 SERP", f"已完成 {len(candidates)} 个候选词查询", total=len(candidates))

        progress.started("keyword.rank", "排序关键词机会", "LLM 正在根据业务与 SERP 证据排序")
        # 排序模型只看到已采集的可追溯证据；最终机会分仍由 Python 规则固定计算。
        rank_payload = [{
            "keyword": candidate.keyword,
            "intent": candidate.intent,
            "business_fit": candidate.business_fit,
            "commercial_proximity": candidate.commercial_proximity,
            "specificity": candidate.specificity,
            "rule_opportunity_score": score,
            "competition": {
                "score": competition.score,
                "level": competition.level,
                "evidence": competition.evidence,
                "serp_complete": serp.complete,
                "error": serp.error,
            },
        } for candidate, serp, competition, score in rows]
        ranked_data = self.llm.chat_json(
            RANK_SYSTEM,
            json.dumps({"keywords": rank_payload}, ensure_ascii=False),
            name="rank_opportunities",
            temperature=0.1,
        )
        progress.completed("keyword.rank", "排序关键词机会", "关键词机会排序完成", total=len(rows))
        rationales = {
            _key(str(item.get("keyword", ""))): str(item.get("rationale", "")).strip()
            for item in ranked_data.get("ranked", []) if isinstance(item, dict)
        }
        order = {
            _key(str(item.get("keyword", ""))): index
            for index, item in enumerate(ranked_data.get("ranked", [])) if isinstance(item, dict)
        }

        opportunities = [KeywordOpportunity(
            keyword=candidate.keyword,
            variants=candidate.variants,
            intent=candidate.intent,
            business_fit=candidate.business_fit,
            commercial_proximity=candidate.commercial_proximity,
            specificity=candidate.specificity,
            competition=competition,
            suggestions=serp.suggestions,
            related_searches=serp.related_searches,
            top_urls=[result.url for result in serp.results if _valid_url(result.url)],
            opportunity_score=score,
            priority=priority(score, candidate.business_fit, competition.level),
            rationale=rationales.get(_key(candidate.keyword)) or candidate.rationale,
            serp_complete=serp.complete,
            serp_error=serp.error,
        ) for candidate, serp, competition, score in rows]
        # 模型给出同分词的业务语义排序；漏返回的词仍保留，并回退到规则分排序。
        opportunities.sort(key=lambda item: (order.get(_key(item.keyword), 999), -item.opportunity_score))

        warnings = [
            "SERP 竞争度是当前百度结果快照的规则估算，不是搜索量或第三方关键词难度。",
            "百度页面结构和访问限制可能导致部分关键词结果不完整。",
        ]
        return KeywordAgentOutput(
            seeds=request.seeds,
            requirement=request.requirement,
            model=self.model_name,
            mode="serp-rule-estimate",
            source_files=source_files or [],
            existing_pages=pages,
            opportunities=opportunities,
            warnings=warnings,
        )

    def expand_candidates(
        self,
        request: KeywordAgentInput,
        *,
        existing_pages: list[dict[str, str]] | None = None,
        progress: ProgressReporter | None = None,
    ) -> list[CandidateKeyword]:
        """只扩展和分类候选词，不触发任何百度自然结果查询。"""
        progress = progress or ProgressReporter()
        pages = existing_pages or []
        progress.started("keyword.expand", "扩展候选词", "LLM 正在扩展、聚类并标注意图")
        expanded = self.llm.chat_json(
            EXPAND_SYSTEM,
            json.dumps({
                "seeds": request.seeds,
                "requirement": request.requirement or "（未提供）",
                "candidate_limit": request.candidate_limit,
                "business_material": request.business_text or "（未提供）",
                "existing_pages": pages,
            }, ensure_ascii=False),
            name="expand_keywords",
            temperature=0.6,
        )
        candidates = _parse_candidates(expanded, request.candidate_limit)
        if not candidates:
            progress.failed("keyword.expand", "扩展候选词", "LLM 没有返回可用的候选关键词")
            raise RuntimeError("LLM 没有返回可用的候选关键词。")
        progress.completed(
            "keyword.expand", "扩展候选词", f"已得到 {len(candidates)} 个去重候选词", total=len(candidates)
        )
        return candidates

    def rank_serp_results(
        self,
        request: KeywordAgentInput,
        candidates: list[CandidateKeyword],
        serp_by_keyword: dict[str, BaiduSERP],
        *,
        source_files: list[str] | None = None,
        existing_pages: list[dict[str, str]] | None = None,
        progress: ProgressReporter | None = None,
    ) -> KeywordAgentOutput:
        """将用户已选择词的 SERP 结果评分、排序；不会自行查询其他候选词。"""
        progress = progress or ProgressReporter()
        rows: list[tuple[CandidateKeyword, BaiduSERP, Any, int]] = []
        for candidate in candidates:
            serp = serp_by_keyword.get(candidate.keyword, BaiduSERP(keyword=candidate.keyword, error="尚未查询"))
            competition = estimate_competition(candidate.keyword, serp.results)
            score = opportunity_score(
                candidate.business_fit, candidate.commercial_proximity, candidate.specificity, competition
            )
            rows.append((candidate, serp, competition, score))

        progress.started("keyword.rank", "排序关键词机会", "LLM 正在根据所选词的 SERP 证据排序")
        rank_payload = [{
            "keyword": candidate.keyword,
            "intent": candidate.intent,
            "business_fit": candidate.business_fit,
            "commercial_proximity": candidate.commercial_proximity,
            "specificity": candidate.specificity,
            "rule_opportunity_score": score,
            "competition": {
                "score": competition.score,
                "level": competition.level,
                "evidence": competition.evidence,
                "serp_complete": serp.complete,
                "error": serp.error,
            },
        } for candidate, serp, competition, score in rows]
        ranked_data = self.llm.chat_json(
            RANK_SYSTEM,
            json.dumps({"keywords": rank_payload}, ensure_ascii=False),
            name="rank_opportunities",
            temperature=0.1,
        ) if rows else {"ranked": []}
        progress.completed("keyword.rank", "排序关键词机会", "关键词机会排序完成", total=len(rows))
        rationales = {
            _key(str(item.get("keyword", ""))): str(item.get("rationale", "")).strip()
            for item in ranked_data.get("ranked", []) if isinstance(item, dict)
        }
        order = {
            _key(str(item.get("keyword", ""))): index
            for index, item in enumerate(ranked_data.get("ranked", [])) if isinstance(item, dict)
        }
        opportunities = [KeywordOpportunity(
            keyword=candidate.keyword,
            variants=candidate.variants,
            intent=candidate.intent,
            business_fit=candidate.business_fit,
            commercial_proximity=candidate.commercial_proximity,
            specificity=candidate.specificity,
            competition=competition,
            suggestions=serp.suggestions,
            related_searches=serp.related_searches,
            top_urls=[result.url for result in serp.results if _valid_url(result.url)],
            opportunity_score=score,
            priority=priority(score, candidate.business_fit, competition.level),
            rationale=rationales.get(_key(candidate.keyword)) or candidate.rationale,
            serp_complete=serp.complete,
            serp_error=serp.error,
        ) for candidate, serp, competition, score in rows]
        opportunities.sort(key=lambda item: (order.get(_key(item.keyword), 999), -item.opportunity_score))
        return KeywordAgentOutput(
            seeds=request.seeds,
            requirement=request.requirement,
            model=self.model_name,
            mode="human-selected-serp",
            source_files=source_files or [],
            existing_pages=existing_pages or [],
            opportunities=opportunities,
            warnings=[
                "只查询了用户勾选的关键词；未勾选词没有请求百度自然结果。",
                "SERP 竞争度是当前百度结果快照的规则估算，不是搜索量或第三方关键词难度。",
            ],
        )


class MockKeywordLLM:
    """固定响应的离线模型，用于 UI 演示和不会消耗 API 配额的测试。"""
    def chat_json(self, system: str, user: str, *, name: str = "call", temperature: float = 0.3) -> dict[str, Any]:
        if name == "expand_keywords":
            return {"candidates": [
                {"keyword": "企业知识库私有化部署", "variants": ["私有化企业知识库部署"], "intent": "transaction", "business_fit": 5, "commercial_proximity": 5, "specificity": 4, "rationale": "直接对应采购与部署需求"},
                {"keyword": "制造业企业知识库解决方案", "variants": [], "intent": "solution", "business_fit": 5, "commercial_proximity": 4, "specificity": 5, "rationale": "行业场景明确"},
                {"keyword": "企业知识库搭建教程", "variants": [], "intent": "informational", "business_fit": 4, "commercial_proximity": 2, "specificity": 3, "rationale": "可承接早期认知需求"},
            ]}
        payload = json.loads(user)
        return {"ranked": [
            {"keyword": item["keyword"], "rationale": f"业务匹配 {item['business_fit']}/5；竞争证据等级 {item['competition']['level']}。"}
            for item in sorted(payload["keywords"], key=lambda row: row["rule_opportunity_score"], reverse=True)
        ]}
