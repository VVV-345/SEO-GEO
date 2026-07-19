"""SERP + 竞品分析 Agent 的输入与输出数据模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompetitorPage:
    """一条 SERP 落地页抓取后的可追溯快照。"""

    rank: int
    requested_url: str
    final_url: str = ""
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    heading_structure: list[dict[str, str | int]] = field(default_factory=list)
    faq_questions: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    case_mentions: list[str] = field(default_factory=list)
    data_points: list[str] = field(default_factory=list)
    text: str = ""
    status_code: int | None = None
    error: str = ""


@dataclass(frozen=True)
class CompetitorAnalysisInput:
    """竞品分析请求；URL 应来自用户确认过的 SERP 结果。"""

    keyword: str
    urls: list[str]
    business_text: str = ""
    existing_pages: list[dict[str, str]] = field(default_factory=list)
    max_pages: int = 10
    max_chars_per_page: int = 12_000


@dataclass(frozen=True)
class CompetitorAnalysisOutput:
    """页面快照和 LLM 归纳出的 Content Brief 前置竞品报告。"""

    keyword: str
    model: str
    pages: list[CompetitorPage]
    search_intent: str
    page_type_summary: list[str]
    common_topics: list[str]
    common_sections: list[str]
    common_faqs: list[str]
    case_evidence: list[str]
    data_evidence: list[str]
    content_gaps: list[str]
    must_cover: list[str]
    recommended_structure: list[str]
    evidence_notes: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """序列化完整结果，并加入供下游 Agent 阅读的字段说明。"""
        payload = asdict(self)
        payload["_field_descriptions"] = {
            "keyword": "本次分析的唯一关键词。",
            "pages": "竞品 URL 的抓取快照；error 非空表示该页未能作为分析证据。",
            "search_intent": "根据成功页面归纳的主搜索意图与页面任务。",
            "page_type_summary": "SERP 前列页面类型的归纳，例如产品页、教程或新闻。",
            "common_topics": "多个成功页面共同覆盖的主题。",
            "common_sections": "多个成功页面常见的标题或内容模块。",
            "common_faqs": "页面中出现或强烈暗示的用户问答主题。",
            "case_evidence": "成功页面中实际提取到的案例角度；不得扩写为客户事实。",
            "data_evidence": "成功页面中实际提取到的数字证据摘要；使用前需回看原页。",
            "content_gaps": "已抓取页面缺少、但基于客户资料可安全补足的内容角度。",
            "must_cover": "下一篇页面必须覆盖的内容清单。",
            "recommended_structure": "供 Content Brief Agent 继续细化的建议结构。",
            "evidence_notes": "分析范围与证据限制，避免把抓取失败误判为内容缺口。",
        }
        return payload
