from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class KeywordAgentInput:
    """关键词 Agent 的原始输入与数量限制。"""
    seeds: list[str]
    requirement: str = ""
    business_text: str = ""
    existing_page_urls: list[str] = field(default_factory=list)
    candidate_limit: int = 30
    serp_limit: int = 10


@dataclass(frozen=True)
class CandidateKeyword:
    """LLM 扩展并按搜索任务去重后的内部候选词。"""
    keyword: str
    variants: list[str]
    intent: str
    business_fit: int
    commercial_proximity: int
    specificity: int
    rationale: str


@dataclass(frozen=True)
class KeywordCandidatePreview:
    """人工选词阶段使用的数据；此时尚未查询百度自然结果 URL。"""

    keyword: str
    variants: list[str]
    intent: str
    business_fit: int
    commercial_proximity: int
    specificity: int
    rationale: str
    suggestions: list[str] = field(default_factory=list)
    suggestion_error: str = ""


@dataclass(frozen=True)
class KeywordCandidateOutput:
    """候选生成阶段的完整输出，供 UI 勾选或保存。"""

    seeds: list[str]
    requirement: str
    business_text: str
    model: str
    source_files: list[str]
    existing_pages: list[dict[str, str]]
    candidates: list[KeywordCandidatePreview]
    warnings: list[str]


@dataclass(frozen=True)
class CompetitionEvidence:
    """根据当前 SERP 快照计算的可解释竞争证据。"""
    score: int
    level: str
    exact_title_ratio: float
    authority_ratio: float
    homepage_ratio: float
    unique_domain_ratio: float
    evidence: list[str]


@dataclass(frozen=True)
class KeywordOpportunity:
    """一个已查询 SERP 的完整关键词机会记录。"""
    keyword: str
    variants: list[str]
    intent: str
    business_fit: int
    commercial_proximity: int
    specificity: int
    competition: CompetitionEvidence
    suggestions: list[str]
    related_searches: list[str]
    top_urls: list[str]
    filtered_urls: list[dict[str, str]]
    opportunity_score: int
    priority: str
    rationale: str
    serp_complete: bool
    serp_error: str = ""


@dataclass(frozen=True)
class KeywordAgentOutput:
    """关键词 Agent 的最终结构化输出。"""
    seeds: list[str]
    requirement: str
    model: str
    mode: str
    source_files: list[str]
    existing_pages: list[dict[str, str]]
    opportunities: list[KeywordOpportunity]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """序列化全部字段并附带下游 Agent 可读的字段说明。"""
        payload = asdict(self)
        payload["_field_descriptions"] = {
            "seeds": "种子词：本次扩词的起点。",
            "requirement": "需求描述：用户希望本次研究重点覆盖或排除的方向。",
            "model": "生成本次结果的 LLM 模型名。",
            "mode": "运行模式与 SERP 评估方式。",
            "source_files": "读取过的客户业务资料文件。",
            "existing_pages": "已读取的客户现有页面内容或抓取错误。",
            "opportunities": "候选关键词机会清单；下一个 Agent 应从这里读取数据。",
            "keyword": "代表关键词：同一搜索任务的主表达。",
            "variants": "近义或同一搜索任务的变体词。",
            "intent": "搜索意图：transaction、commercial、solution 或 informational。",
            "business_fit": "业务匹配度：1-5 分，越高越符合客户业务。",
            "commercial_proximity": "商业接近程度：1-5 分，越高越接近咨询或购买。",
            "specificity": "长尾具体度：1-5 分，越高越具体。",
            "competition": "基于当前百度 SERP 快照的规则竞争估算，不是真实关键词难度。",
            "suggestions": "百度下拉联想词。",
            "related_searches": "百度相关搜索词。",
            "top_urls": "本次 SERP 获取到的前列页面 URL，进入竞品分析前需再次校验。",
            "filtered_urls": "因广告、中转或搜索聚合属性被排除的 URL 及具体原因。",
            "opportunity_score": "关键词机会分：业务价值与 SERP 可进入性的组合分数。",
            "priority": "业务优先级：P1、P2 或 P3。",
            "rationale": "本次排序理由。",
            "serp_complete": "是否取得足够的 SERP 自然结果。",
            "serp_error": "SERP 抓取异常或不完整原因。",
        }
        return payload
