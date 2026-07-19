"""抓取 SERP 页面并形成竞品内容证据报告。"""
from __future__ import annotations

import json
from typing import Any, Callable

from core.llm import JSONLLM
from tools.progress import ProgressReporter
from tools.webpage import WebPageContent, fetch_webpage

from .models import CompetitorAnalysisInput, CompetitorAnalysisOutput, CompetitorPage
from .prompts import ANALYZE_SYSTEM


PageFetcher = Callable[..., WebPageContent]


def _strings(value: Any, *, limit: int = 12) -> list[str]:
    """容错读取模型数组，过滤空值和重复项，避免报告出现异常 JSON 内容。"""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


class SerpCompetitorAgent:
    """一个关键词的竞品流程：抓取确认 URL → 整理页面证据 → LLM 生成报告。"""

    def __init__(self, llm: JSONLLM, *, model_name: str = "unknown", page_fetcher: PageFetcher = fetch_webpage) -> None:
        """保存模型和可替换抓取器，方便在测试中注入离线网页快照。"""
        self.llm = llm
        self.model_name = model_name
        self.page_fetcher = page_fetcher

    def collect_pages(
        self, request: CompetitorAnalysisInput, *, progress: ProgressReporter | None = None
    ) -> list[CompetitorPage]:
        """逐页抓取用户已确认的 URL；单页失败只记录错误，不中断整项分析。"""
        progress = progress or ProgressReporter()
        urls = list(dict.fromkeys(url.strip() for url in request.urls if url.strip()))[:request.max_pages]
        pages: list[CompetitorPage] = []
        progress.started("competitor.fetch", "抓取竞品页面", "开始抓取已确认的 SERP 落地页", total=len(urls))
        for rank, url in enumerate(urls, 1):
            progress.step("competitor.fetch", "抓取竞品页面", f"正在抓取：{url}", current=rank - 1, total=len(urls))
            try:
                content = self.page_fetcher(url, max_chars=request.max_chars_per_page)
                page = CompetitorPage(
                    rank=rank,
                    requested_url=url,
                    final_url=content.final_url,
                    title=content.title,
                    description=content.description,
                    headings=content.headings[:80],
                    heading_structure=content.heading_structure[:80],
                    faq_questions=content.faq_questions[:30],
                    tables=content.tables[:12],
                    case_mentions=content.case_mentions[:20],
                    data_points=content.data_points[:30],
                    text=content.text[:request.max_chars_per_page],
                    status_code=content.status_code,
                )
                status = f"已抓取：{content.title or content.final_url}"
            except Exception as error:
                page = CompetitorPage(rank=rank, requested_url=url, error=str(error))
                status = f"抓取失败：{url}"
            pages.append(page)
            progress.step("competitor.fetch", "抓取竞品页面", status, current=rank, total=len(urls))
        progress.completed("competitor.fetch", "抓取竞品页面", f"已处理 {len(pages)} 个 URL", total=len(urls))
        return pages

    def run(
        self, request: CompetitorAnalysisInput, *, progress: ProgressReporter | None = None
    ) -> CompetitorAnalysisOutput:
        """执行完整竞品分析，并返回所有页面快照与结构化策略结论。"""
        if not request.keyword.strip():
            raise ValueError("竞品分析必须提供一个关键词。")
        if not request.urls:
            raise ValueError("竞品分析必须提供至少一个已确认的 SERP URL。")
        progress = progress or ProgressReporter()
        pages = self.collect_pages(request, progress=progress)
        successful = [page for page in pages if not page.error and page.text]
        progress.started("competitor.analyze", "分析竞品内容", "LLM 正在归纳共同主题与内容缺口")
        payload = {
            "keyword": request.keyword,
            "business_material": request.business_text or "（未提供）",
            "existing_pages": request.existing_pages,
            "successful_page_count": len(successful),
            "failed_page_count": len(pages) - len(successful),
            "pages": [
                {
                    "rank": page.rank,
                    "url": page.final_url or page.requested_url,
                    "title": page.title,
                    "description": page.description,
                    "headings": page.headings,
                    "heading_structure": page.heading_structure,
                    "faq_questions": page.faq_questions,
                    "tables": page.tables,
                    "case_mentions": page.case_mentions,
                    "data_points": page.data_points,
                    "content": page.text,
                }
                for page in successful
            ],
        }
        analysis = self.llm.chat_json(
            ANALYZE_SYSTEM, json.dumps(payload, ensure_ascii=False), name="analyze_competitors", temperature=0.2
        ) if successful else {}
        progress.completed("competitor.analyze", "分析竞品内容", "竞品内容分析完成")
        warnings = ["结论仅基于本次成功抓取的 SERP 页面快照，不代表全网竞争情况。"]
        if len(successful) < 3:
            warnings.append(f"仅成功抓取 {len(successful)} 页，证据不足；内容缺口需要人工复核。")
        if len(successful) < len(pages):
            warnings.append(f"有 {len(pages) - len(successful)} 个 URL 抓取失败，详见 pages.json。")
        return CompetitorAnalysisOutput(
            keyword=request.keyword,
            model=self.model_name,
            pages=pages,
            search_intent=str(analysis.get("search_intent", "")).strip(),
            page_type_summary=_strings(analysis.get("page_type_summary")),
            common_topics=_strings(analysis.get("common_topics")),
            common_sections=_strings(analysis.get("common_sections")),
            common_faqs=_strings(analysis.get("common_faqs")),
            case_evidence=_strings(analysis.get("case_evidence")),
            data_evidence=_strings(analysis.get("data_evidence")),
            content_gaps=_strings(analysis.get("content_gaps")),
            must_cover=_strings(analysis.get("must_cover")),
            recommended_structure=_strings(analysis.get("recommended_structure"), limit=20),
            evidence_notes=_strings(analysis.get("evidence_notes")),
            warnings=warnings,
        )


class MockCompetitorLLM:
    """离线演示和测试所用的固定竞品归纳模型，不消耗外部 API 配额。"""

    def chat_json(self, system: str, user: str, *, name: str = "call", temperature: float = 0.3) -> dict[str, Any]:
        """返回字段完整、但不假装来自真实竞品页面的示例结论。"""
        del system, user, name, temperature
        return {
            "search_intent": "Mock 模式未分析真实搜索意图",
            "page_type_summary": ["Mock 模式：未访问真实竞品页面"],
            "common_topics": [],
            "common_sections": [],
            "common_faqs": [],
            "case_evidence": [],
            "data_evidence": [],
            "content_gaps": [],
            "must_cover": ["真实运行后依据成功抓取页面重新生成"],
            "recommended_structure": [],
            "evidence_notes": ["Mock 模式仅验证流程，不能作为竞品结论。"],
        }
