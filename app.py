"""应用服务层：为 CLI 和 UI 组装资料工具、页面工具与 Agent。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.keyword_agent import (
    KeywordAgent,
    KeywordAgentInput,
    KeywordAgentOutput,
    KeywordCandidateOutput,
    KeywordCandidatePreview,
    MockKeywordLLM,
)
from agents.keyword_agent.models import CandidateKeyword
from core.config import load_llm_config
from core.llm import OpenAILLM
from tools.baidu_serp import BaiduSERP, BaiduSERPClient
from tools.baidu_browser import BaiduBrowserFallback
from tools.file_reader import combine_documents, read_documents
from tools.progress import ProgressReporter
from tools.serp_url_tool import SerpURLTool, collect_suggestions
from tools.webpage import fetch_webpage


class OfflineSERPClient:
    """Mock 模式的 SERP 替身，保证测试不会产生外部网络请求。"""

    def search(self, keyword: str, *, limit: int = 10) -> BaiduSERP:
        return BaiduSERP(keyword=keyword, error="Mock 模式未访问百度")


def _load_keyword_inputs(
    *,
    material_files: list[str],
    page_urls: list[str],
    inline_business_text: str,
    mock: bool,
    progress: ProgressReporter,
) -> tuple[str, list[dict[str, str]]]:
    """读取并清洗候选生成所需资料；两个工作流入口复用同一输入规则。"""
    progress.started("input.materials", "读取业务资料", "开始读取客户业务资料", total=len(material_files))
    documents = read_documents(material_files) if material_files else []
    parts = [inline_business_text.strip(), combine_documents(documents)]
    business_text = "\n\n".join(part for part in parts if part)[:120_000]
    progress.completed(
        "input.materials", "读取业务资料", f"已读取 {len(documents)} 个资料文件", total=len(material_files)
    )

    pages: list[dict[str, str]] = []
    page_content_chars = 0
    if page_urls:
        progress.started("input.pages", "解析客户页面", "开始读取客户已有页面", total=len(page_urls))
    for index, url in enumerate(page_urls, 1):
        if mock:
            pages.append({"url": url, "note": "Mock 模式未访问该页面"})
            progress.step("input.pages", "解析客户页面", f"Mock 跳过页面：{url}", current=index, total=len(page_urls))
            continue
        progress.step("input.pages", "解析客户页面", f"正在解析：{url}", current=index - 1, total=len(page_urls))
        try:
            page = fetch_webpage(url)
            remaining = max(0, 40_000 - page_content_chars)
            content = page.text[:min(12_000, remaining)]
            page_content_chars += len(content)
            pages.append({
                "url": page.final_url,
                "title": page.title,
                "description": page.description,
                "headings": " | ".join(page.headings[:20]),
                "content": content,
            })
        except Exception as error:
            pages.append({"url": url, "error": str(error)})
        progress.step("input.pages", "解析客户页面", f"已处理：{url}", current=index, total=len(page_urls))
    if page_urls:
        progress.completed("input.pages", "解析客户页面", f"已处理 {len(page_urls)} 个客户页面", total=len(page_urls))
    return business_text, pages


def generate_keyword_candidates(
    *,
    seeds: list[str],
    requirement: str = "",
    material_files: list[str] | None = None,
    page_urls: list[str] | None = None,
    inline_business_text: str = "",
    candidate_limit: int = 30,
    mock: bool = False,
    progress: ProgressReporter | None = None,
) -> KeywordCandidateOutput:
    """阶段一：生成候选词、意图分类和下拉词；不查询任何自然结果 URL。"""
    progress = progress or ProgressReporter()
    files, urls = material_files or [], page_urls or []
    business_text, pages = _load_keyword_inputs(
        material_files=files,
        page_urls=urls,
        inline_business_text=inline_business_text,
        mock=mock,
        progress=progress,
    )
    if mock:
        llm, model = MockKeywordLLM(), "mock"
    else:
        config = load_llm_config()
        llm, model = OpenAILLM(config), config.model
    agent = KeywordAgent(llm, model_name=model)
    request = KeywordAgentInput(
        seeds=seeds,
        requirement=requirement,
        business_text=business_text,
        existing_page_urls=urls,
        candidate_limit=candidate_limit,
    )
    candidates = agent.expand_candidates(request, existing_pages=pages, progress=progress)

    progress.started("keyword.suggestions", "获取百度下拉词", "开始获取候选词的百度下拉词", total=len(candidates))
    suggestion_map = (
        {candidate.keyword: ([], "Mock 模式未访问百度") for candidate in candidates}
        if mock
        else collect_suggestions([candidate.keyword for candidate in candidates])
    )
    previews = []
    for index, candidate in enumerate(candidates, 1):
        suggestions, error = suggestion_map.get(candidate.keyword, ([], "未查询"))
        previews.append(KeywordCandidatePreview(
            keyword=candidate.keyword,
            variants=candidate.variants,
            intent=candidate.intent,
            business_fit=candidate.business_fit,
            commercial_proximity=candidate.commercial_proximity,
            specificity=candidate.specificity,
            rationale=candidate.rationale,
            suggestions=suggestions,
            suggestion_error=error,
        ))
        progress.step(
            "keyword.suggestions", "获取百度下拉词", f"已处理：{candidate.keyword}",
            current=index, total=len(candidates),
        )
    progress.completed(
        "keyword.suggestions", "获取百度下拉词", "候选词预览已生成", total=len(candidates)
    )
    return KeywordCandidateOutput(
        seeds=seeds,
        requirement=requirement,
        business_text=business_text,
        model=model,
        source_files=files,
        existing_pages=pages,
        candidates=previews,
        warnings=["候选阶段尚未查询自然结果 URL；只有用户勾选的词才会进入下一步。"],
    )


def fetch_keyword_serp(
    keywords: list[str],
    *,
    serp_limit: int = 10,
    mock: bool = False,
    progress: ProgressReporter | None = None,
) -> dict[str, BaiduSERP]:
    """阶段二工具入口：仅查询传入的词；可传单个词实现独立重试。"""
    progress = progress or ProgressReporter()
    progress.started("keyword.serp", "获取所选词 URL", "开始查询用户勾选的关键词", total=len(keywords))
    if mock:
        results = {keyword: BaiduSERP(keyword=keyword, error="Mock 模式未访问百度") for keyword in keywords}
    else:
        with SerpURLTool() as tool:
            results = tool.fetch_many(
                keywords,
                limit=serp_limit,
                on_item=lambda current, total, keyword, result: progress.step(
                    "keyword.serp",
                    "获取所选词 URL",
                    f"{'成功' if result.results else '未取得URL'}：{keyword}",
                    current=current,
                    total=total,
                ),
            )
    progress.completed("keyword.serp", "获取所选词 URL", "所选关键词查询完成", total=len(keywords))
    return results


def build_selected_keyword_output(
    candidates_output: KeywordCandidateOutput,
    selected_keywords: list[str],
    serp_by_keyword: dict[str, BaiduSERP],
    *,
    mock: bool = False,
    progress: ProgressReporter | None = None,
) -> KeywordAgentOutput:
    """将已勾选词和各自SERP结果整理为最终机会报告。"""
    progress = progress or ProgressReporter()
    preview_by_keyword = {candidate.keyword: candidate for candidate in candidates_output.candidates}
    selected = []
    for keyword in selected_keywords:
        preview = preview_by_keyword.get(keyword)
        if preview is None:
            continue
        selected.append(CandidateKeyword(
            keyword=preview.keyword,
            variants=preview.variants,
            intent=preview.intent,
            business_fit=preview.business_fit,
            commercial_proximity=preview.commercial_proximity,
            specificity=preview.specificity,
            rationale=preview.rationale,
        ))
        serp = serp_by_keyword.get(keyword)
        if serp is not None and not serp.suggestions:
            serp_by_keyword[keyword] = BaiduSERP(
                keyword=serp.keyword,
                suggestions=preview.suggestions,
                related_searches=serp.related_searches,
                results=serp.results,
                complete=serp.complete,
                error=serp.error,
            )
    if mock:
        llm, model = MockKeywordLLM(), "mock"
    else:
        config = load_llm_config()
        llm, model = OpenAILLM(config), config.model
    agent = KeywordAgent(llm, model_name=model)
    request = KeywordAgentInput(
        seeds=candidates_output.seeds,
        requirement=candidates_output.requirement,
        business_text=candidates_output.business_text,
        candidate_limit=len(selected),
    )
    return agent.rank_serp_results(
        request,
        selected,
        serp_by_keyword,
        source_files=candidates_output.source_files,
        existing_pages=candidates_output.existing_pages,
        progress=progress,
    )


def run_keyword_workflow(
    *,
    seeds: list[str],
    material_files: list[str] | None = None,
    page_urls: list[str] | None = None,
    requirement: str = "",
    inline_business_text: str = "",
    candidate_limit: int = 30,
    serp_limit: int = 10,
    mock: bool = False,
    progress: ProgressReporter | None = None,
) -> KeywordAgentOutput:
    """组装关键词工作流；CLI 和桌面 UI 都通过此函数调用，避免两处逻辑分叉。"""
    progress = progress or ProgressReporter()
    files = material_files or []
    urls = page_urls or []

    progress.started("input.materials", "读取业务资料", "开始读取客户业务资料", total=len(files))
    documents = read_documents(files) if files else []
    # 保留来源文件名，便于模型区分不同资料；总长度受限以控制上下文和调用成本。
    parts = [inline_business_text.strip(), combine_documents(documents)]
    business_text = "\n\n".join(part for part in parts if part)[:120_000]
    progress.completed("input.materials", "读取业务资料", f"已读取 {len(documents)} 个资料文件", total=len(files))

    pages: list[dict[str, str]] = []
    # 每个页面最多给模型 12k 字，所有页面合计最多 40k 字，防止长页面挤占业务资料。
    page_content_chars = 0
    if urls:
        progress.started("input.pages", "解析客户页面", "开始读取客户已有页面", total=len(urls))
    for index, url in enumerate(urls, 1):
        if mock:
            pages.append({"url": url, "note": "Mock 模式未访问该页面"})
            progress.step(
                "input.pages", "解析客户页面", f"Mock 跳过页面：{url}", current=index, total=len(urls)
            )
            continue
        progress.step("input.pages", "解析客户页面", f"正在解析：{url}", current=index - 1, total=len(urls))
        try:
            page = fetch_webpage(url)
            remaining = max(0, 40_000 - page_content_chars)
            content = page.text[:min(12_000, remaining)]
            page_content_chars += len(content)
            pages.append({
                "url": page.final_url,
                "title": page.title,
                "description": page.description,
                "headings": " | ".join(page.headings[:20]),
                "content": content,
            })
        except Exception as error:
            # 单个 URL 失败不阻断整个研究任务，错误会随输出交给人工复核。
            pages.append({"url": url, "error": str(error)})
        progress.step("input.pages", "解析客户页面", f"已处理：{url}", current=index, total=len(urls))
    if urls:
        progress.completed("input.pages", "解析客户页面", f"已处理 {len(urls)} 个客户页面", total=len(urls))

    if mock:
        llm, serp_client, model = MockKeywordLLM(), OfflineSERPClient(), "mock"
    else:
        config = load_llm_config()
        llm = OpenAILLM(config)
        serp_client = BaiduSERPClient(browser_fallback=BaiduBrowserFallback())
        model = config.model
    agent = KeywordAgent(llm, serp_client, model_name=model)
    progress.started("keyword.workflow", "关键词 Agent", "开始执行关键词机会研究")
    try:
        output = agent.run(
            KeywordAgentInput(
                seeds=seeds,
                requirement=requirement,
                business_text=business_text,
                existing_page_urls=urls,
                candidate_limit=candidate_limit,
                serp_limit=serp_limit,
            ),
            source_files=files,
            existing_pages=pages,
            progress=progress,
        )
    except Exception as error:
        progress.failed("keyword.workflow", "关键词 Agent", str(error))
        raise
    finally:
        close = getattr(serp_client, "close", None)
        if callable(close):
            close()
    progress.completed("keyword.workflow", "关键词 Agent", f"已生成 {len(output.opportunities)} 个关键词机会")
    return output


def write_keyword_output(output: KeywordAgentOutput, output_dir: str | Path = "output") -> tuple[Path, Path]:
    """同时写机器可读 JSON 和供人工决策的精简 Markdown 报告。"""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\s/\\:*?"<>|]+', "_", "_".join(output.seeds)).strip("_")[:60] or "keywords"
    json_path = directory / f"keyword_opportunities_{name}.json"
    markdown_path = directory / f"keyword_opportunities_{name}.md"
    json_path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 关键词机会清单：{', '.join(output.seeds)}", ""]
    lines.extend(f"> {warning}" for warning in output.warnings)
    lines.extend([
        "",
        f"- 本次模型：{output.model}",
        f"- 需求描述：{output.requirement or '未提供'}",
        f"- 业务资料：{'、'.join(output.source_files) or '未提供'}",
        f"- 已有页面：{_existing_pages_summary(output.existing_pages)}",
        "- 报告保留了 JSON 中每个关键词的全部采集字段；JSON 继续作为下游 Agent 的结构化输入。",
    ])

    for group in ("P1", "P2", "P3", "待验证"):
        items = [item for item in output.opportunities if item.priority == group]
        if not items:
            continue
        title = "待验证（未取得足够的百度自然结果）" if group == "待验证" else group
        lines.extend(["", f"## {title}", ""])
        for index, item in enumerate(items):
            if index:
                lines.extend(["", "---", ""])
            lines.extend(_render_keyword_details(item, markdown=True))
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def render_keyword_report(output: KeywordAgentOutput) -> str:
    """生成 UI 使用的完整整理报告；保留 JSON 采集信息，但按人工阅读顺序排版。"""
    lines = [
        f"关键词机会清单：{'、'.join(output.seeds)}",
        "",
        "说明：P1/P2/P3 是业务候选优先级；“待验证”表示本次没有取得足够的百度自然结果，不能据此判断竞争。",
        f"模型：{output.model} ｜ 业务资料：{len(output.source_files)} 个 ｜ 客户页面：{len(output.existing_pages)} 个",
        f"需求描述：{output.requirement or '未提供'}",
        f"已有页面：{_existing_pages_summary(output.existing_pages)}",
    ]
    for group in ("P1", "P2", "P3", "待验证"):
        items = [item for item in output.opportunities if item.priority == group]
        if not items:
            continue
        title = "待验证：请重新查询或人工查看百度前10" if group == "待验证" else group
        lines.extend(["", f"{title}（{len(items)}）"])
        for index, item in enumerate(items):
            if index:
                lines.extend(["", "=" * 72, ""])
            lines.extend(_render_keyword_details(item, markdown=False))
    return "\n".join(lines)


def render_candidate_report(output: KeywordCandidateOutput) -> str:
    """阶段一报告：按意图展示候选、业务评分与下拉词，明确尚未查询URL。"""
    lines = [
        f"候选拓展词：{'、'.join(output.seeds)}",
        f"需求描述：{output.requirement or '未提供'}",
        "说明：以下候选尚未查询自然结果。请在上方表格勾选，再点击“获取勾选词 URL”。",
    ]
    labels = {
        "transaction": "明确采购/咨询",
        "commercial": "选型/对比",
        "solution": "解决方案",
        "informational": "知识了解",
    }
    for intent in ("transaction", "commercial", "solution", "informational"):
        items = [item for item in output.candidates if item.intent == intent]
        if not items:
            continue
        lines.extend(["", f"【{labels[intent]}】（{len(items)}）"])
        for index, item in enumerate(items):
            if index:
                lines.extend(["", "-" * 56])
            lines.extend([
                f"拓展词：{item.keyword}",
                f"业务评分：匹配度 {item.business_fit}/5 ｜ 商业接近 {item.commercial_proximity}/5 ｜ 具体度 {item.specificity}/5",
                f"近义表达：{'、'.join(item.variants) if item.variants else '无'}",
                f"扩展理由：{item.rationale}",
                f"百度下拉词（{len(item.suggestions)}）：{'、'.join(item.suggestions) if item.suggestions else '未获取'}",
                f"下拉词状态：{item.suggestion_error or '成功'}",
                "URL状态：尚未查询",
            ])
    return "\n".join(lines)


def _render_keyword_details(item: KeywordOpportunity, *, markdown: bool) -> list[str]:
    """将单个机会的全部 JSON 信息按“决策→证据→原始采集”顺序呈现。

    不删减下拉词、相关搜索或 URL；这些字段在 UI 中较长，但仍是后续竞品研究的证据。
    """
    prefix = "### 拓展词：" if markdown else "\n拓展词："
    indent = "- " if markdown else "  "
    urls = item.top_urls or []
    suggestions = item.suggestions or []
    related = item.related_searches or []
    variants = item.variants or []
    lines = [
        f"{prefix}{item.keyword}",
        f"{indent}优先级：{item.priority} ｜ 搜索意图：{_intent_label(item.intent)} ｜ 机会分：{item.opportunity_score}/100",
        f"{indent}业务评分：匹配度 {item.business_fit}/5 ｜ 商业接近 {item.commercial_proximity}/5 ｜ 具体度 {item.specificity}/5",
        f"{indent}SERP 竞争：{_competition_label(item.competition.level, item.competition.score)} ｜ "
        f"自然结果：{'完整' if item.serp_complete else '不完整'}",
        f"{indent}入选原因：{item.rationale}",
        f"{indent}下一步：{_next_action(item)}",
        f"{indent}近义表达（{len(variants)}）：{'、'.join(variants) if variants else '无'}",
        f"{indent}竞争证据：{'；'.join(item.competition.evidence) if item.competition.evidence else '无'}",
        f"{indent}竞争指标：标题覆盖 {item.competition.exact_title_ratio:.0%} ｜ "
        f"强势域名 {item.competition.authority_ratio:.0%} ｜ 首页 {item.competition.homepage_ratio:.0%} ｜ "
        f"独立域名 {item.competition.unique_domain_ratio:.0%}",
        f"{indent}百度下拉词（{len(suggestions)}）：{'、'.join(suggestions) if suggestions else '未获取'}",
        f"{indent}百度相关搜索（{len(related)}）：{'、'.join(related) if related else '未获取'}",
        f"{indent}SERP 前列 URL（{len(urls)}）：",
    ]
    lines.extend(f"  {index}. {url}" for index, url in enumerate(urls, 1))
    if not urls:
        lines.append(f"  无（{item.serp_error or '本次未取得落地页 URL'}）")
    if item.serp_error:
        lines.append(f"{indent}SERP 抓取提示：{item.serp_error}")
    return lines


def _intent_label(intent: str) -> str:
    return {
        "transaction": "明确采购/咨询",
        "commercial": "选型/对比",
        "solution": "解决方案",
        "informational": "知识了解",
    }.get(intent, intent or "未分类")


def _existing_pages_summary(pages: list[dict[str, str]]) -> str:
    if not pages:
        return "未提供"
    succeeded = sum(1 for page in pages if not page.get("error"))
    failed = len(pages) - succeeded
    return f"已提供 {len(pages)} 个，解析成功 {succeeded} 个，失败 {failed} 个"


def _competition_label(level: str, score: int) -> str:
    return {"low": "较低", "medium": "中等", "high": "较高", "unknown": "未验证"}.get(level, level) + f"（{score}/100）"


def _next_action(item: KeywordOpportunity) -> str:
    if not item.serp_complete:
        return "重新查询或人工查看百度前10；确认后再决定是否进入竞品分析。"
    if item.priority == "P1":
        return "进入 SERP + 竞品分析 Agent，抓取前10页面后制作 Content Brief。"
    if item.priority == "P2":
        return "保留在内容选题池；P1确认后再安排竞品分析。"
    return "暂不立项；后续有真实搜索量或行业案例后再复评。"
