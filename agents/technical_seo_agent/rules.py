"""加载技术 SEO 知识库，并将网站事实匹配为可追溯问题。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import AuditFinding, AuditRule, RuleReference, SiteAuditSnapshot, TechnicalAuditInput


KNOWLEDGE_FILE = Path(__file__).with_name("knowledge") / "core_rules.json"


def load_rules(path: str | Path = KNOWLEDGE_FILE) -> tuple[str, dict[str, AuditRule]]:
    """读取本地规则卡片并返回版本号和按 rule_id 索引的规则。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules: dict[str, AuditRule] = {}
    for item in data.get("rules", []):
        references = [RuleReference(**reference) for reference in item.get("references", [])]
        rule = AuditRule(
            rule_id=item["rule_id"],
            category=item["category"],
            title=item["title"],
            default_priority=item["default_priority"],
            confidence=item["confidence"],
            impact=item["impact"],
            fix=item["fix"],
            validation=item["validation"],
            scope=item["scope"],
            limitations=list(item.get("limitations", [])),
            references=references,
        )
        if rule.rule_id in rules:
            raise ValueError(f"技术 SEO 规则 ID 重复：{rule.rule_id}")
        rules[rule.rule_id] = rule
    return str(data.get("version", "unknown")), rules


def _finding(
    rule: AuditRule,
    *,
    suffix: str,
    urls: list[str],
    evidence: list[dict[str, Any]],
    priority: str | None = None,
    business_reason: str = "",
) -> AuditFinding:
    """用规则卡片和观察值构造统一问题对象。"""
    return AuditFinding(
        finding_id=f"{rule.rule_id}:{suffix}",
        rule_id=rule.rule_id,
        category=rule.category,
        priority=priority or rule.default_priority,
        confidence=rule.confidence,
        title=rule.title,
        affected_urls=urls,
        evidence=evidence,
        impact=rule.impact,
        fix=rule.fix,
        validation=rule.validation,
        references=rule.references,
        business_reason=business_reason,
    )


def match_rules(
    snapshot: SiteAuditSnapshot,
    request: TechnicalAuditInput,
    rules: dict[str, AuditRule],
) -> list[AuditFinding]:
    """用确定性 Python 条件匹配规则；建议性规则明确保留较低置信度。"""
    findings: list[AuditFinding] = []
    page_by_url = {page.url: page for page in snapshot.pages}
    core = set(request.core_urls)

    robots_rule = rules["robots_unavailable"]
    if snapshot.robots_status is None or snapshot.robots_status >= 500 or snapshot.robots_status == 429:
        findings.append(_finding(
            robots_rule,
            suffix="site",
            urls=[snapshot.robots_url],
            evidence=[{"status_code": snapshot.robots_status, "error": snapshot.robots_error}],
        ))

    blocked_rule = rules["robots_blocks_core_url"]
    for page in snapshot.pages:
        if page.robots_allowed is False and page.url in core:
            findings.append(_finding(
                blocked_rule,
                suffix=str(len(findings) + 1),
                urls=[page.url],
                evidence=[{"robots_allowed": False, "error": page.error}],
                business_reason="该 URL 由用户明确标记为核心页面。",
            ))

    sitemap_blocked = [page for page in snapshot.pages if page.in_sitemap and page.robots_allowed is False]
    if sitemap_blocked:
        rule = rules["robots_blocks_sitemap_url"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in sitemap_blocked],
            evidence=[{
                "url": page.url, "in_sitemap": True, "robots_allowed": False
            } for page in sitemap_blocked],
        ))

    good_sitemap = any(
        isinstance(status, int)
        and status < 400
        and not any(error.startswith(f"{url}:") for error in snapshot.sitemap_errors)
        for url, status in snapshot.sitemap_statuses.items()
    )
    if not good_sitemap:
        rule = rules["sitemap_unavailable"]
        findings.append(_finding(
            rule,
            suffix="site",
            urls=snapshot.sitemap_urls or [snapshot.root_url + "sitemap.xml"],
            evidence=[{"statuses": snapshot.sitemap_statuses, "errors": snapshot.sitemap_errors}],
        ))

    error_pages = [page for page in snapshot.pages if page.status_code is not None and page.status_code >= 400]
    core_error_pages = [page for page in error_pages if page.url in core]
    other_error_pages = [page for page in error_pages if page.url not in core]
    if core_error_pages:
        rule = rules["http_error_page"]
        findings.append(_finding(
            rule,
            suffix="core-pages",
            urls=[page.url for page in core_error_pages],
            evidence=[{"url": page.url, "status_code": page.status_code} for page in core_error_pages],
            priority="P0",
            business_reason="这些错误 URL 由用户明确指定为核心页面。",
        ))
    if other_error_pages:
        rule = rules["http_error_page"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in other_error_pages],
            evidence=[{"url": page.url, "status_code": page.status_code} for page in other_error_pages],
        ))

    html_pages = [
        page for page in snapshot.pages
        if page.status_code is not None and 200 <= page.status_code < 300 and "html" in page.content_type.lower()
    ]
    indexable_html_pages = [page for page in html_pages if "noindex" not in page.robots_meta]
    missing_titles = [page for page in indexable_html_pages if not page.title]
    if missing_titles:
        rule = rules["missing_title"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in missing_titles],
            evidence=[{"url": page.url, "title": page.title} for page in missing_titles],
        ))

    title_groups: dict[str, list[str]] = defaultdict(list)
    original_titles: dict[str, str] = {}
    for page in indexable_html_pages:
        key = " ".join(page.title.lower().split())
        if key:
            title_groups[key].append(page.url)
            original_titles[key] = page.title
    duplicates = [(key, urls) for key, urls in title_groups.items() if len(urls) > 1]
    if duplicates:
        rule = rules["duplicate_title"]
        findings.append(_finding(
            rule,
            suffix="groups",
            urls=list(dict.fromkeys(url for _, urls in duplicates for url in urls)),
            evidence=[{"title": original_titles[key], "urls": urls} for key, urls in duplicates],
        ))

    missing_h1 = [page for page in indexable_html_pages if not page.h1]
    if missing_h1:
        rule = rules["missing_h1"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in missing_h1],
            evidence=[{"url": page.url, "h1_count": 0} for page in missing_h1],
        ))

    missing_descriptions = [page for page in indexable_html_pages if not page.meta_description]
    if missing_descriptions:
        rule = rules["missing_meta_description"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in missing_descriptions],
            evidence=[{"url": page.url, "meta_description": ""} for page in missing_descriptions],
        ))

    description_groups: dict[str, list[str]] = defaultdict(list)
    original_descriptions: dict[str, str] = {}
    for page in indexable_html_pages:
        key = " ".join(page.meta_description.lower().split())
        if key:
            description_groups[key].append(page.url)
            original_descriptions[key] = page.meta_description
    duplicate_descriptions = [
        (key, urls) for key, urls in description_groups.items() if len(urls) > 1
    ]
    if duplicate_descriptions:
        rule = rules["duplicate_meta_description"]
        findings.append(_finding(
            rule,
            suffix="groups",
            urls=list(dict.fromkeys(
                url for _, urls in duplicate_descriptions for url in urls
            )),
            evidence=[{
                "meta_description": original_descriptions[key], "urls": urls
            } for key, urls in duplicate_descriptions],
        ))

    missing_canonical = [page for page in indexable_html_pages if not page.canonical]
    if missing_canonical:
        rule = rules["missing_canonical"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in missing_canonical],
            evidence=[{"url": page.url, "canonical": ""} for page in missing_canonical],
        ))

    root_host = (urlparse(snapshot.root_url).hostname or "").lower().removeprefix("www.")
    cross_domain = [
        page for page in indexable_html_pages
        if page.canonical
        and (urlparse(page.canonical).hostname or "").lower().removeprefix("www.") != root_host
    ]
    if cross_domain:
        rule = rules["cross_domain_canonical"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in cross_domain],
            evidence=[{"url": page.url, "canonical": page.canonical} for page in cross_domain],
        ))

    known_status = {
        page.url: page.status_code for page in snapshot.pages if page.status_code is not None
    }
    bad_canonical = [
        page for page in indexable_html_pages
        if page.canonical and known_status.get(page.canonical, 0) >= 400
    ]
    if bad_canonical:
        rule = rules["canonical_target_error"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in bad_canonical],
            evidence=[{
                "url": page.url,
                "canonical": page.canonical,
                "canonical_status": known_status[page.canonical],
            } for page in bad_canonical],
        ))

    core_noindex = [
        page for page in html_pages if page.url in core and "noindex" in page.robots_meta
    ]
    if core_noindex:
        rule = rules["core_url_noindex"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in core_noindex],
            evidence=[{"url": page.url, "robots_meta": page.robots_meta} for page in core_noindex],
            business_reason="这些 URL 由用户明确指定为需要参与搜索的核心页面。",
        ))

    noindex_pages = [
        page for page in html_pages if page.in_sitemap and "noindex" in page.robots_meta
    ]
    if noindex_pages:
        rule = rules["noindex_in_sitemap"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in noindex_pages],
            evidence=[{
                "url": page.url, "robots_meta": page.robots_meta, "in_sitemap": True
            } for page in noindex_pages],
        ))

    broken_targets = {
        page.url: page.status_code for page in snapshot.pages
        if page.status_code is not None and page.status_code >= 400
    }
    broken_evidence = []
    for source in snapshot.pages:
        for target in source.internal_links:
            if target in broken_targets:
                broken_evidence.append({
                    "source_url": source.url,
                    "target_url": target,
                    "target_status": broken_targets[target],
                })
    if broken_evidence:
        rule = rules["broken_internal_link"]
        findings.append(_finding(
            rule,
            suffix="links",
            urls=list(dict.fromkeys(item["source_url"] for item in broken_evidence)),
            evidence=broken_evidence,
        ))

    orphan_pages = [
        page for page in indexable_html_pages
        if page.in_sitemap and page.url != snapshot.root_url and snapshot.inlink_counts.get(page.url, 0) == 0
    ]
    if orphan_pages:
        rule = rules["orphan_sitemap_page"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in orphan_pages],
            evidence=[{
                "url": page.url, "in_sitemap": True, "observed_inlinks": 0,
                "crawl_page_count": len(snapshot.pages),
            } for page in orphan_pages],
        ))

    invalid_schema = [page for page in html_pages if page.schema_errors]
    if invalid_schema:
        rule = rules["invalid_json_ld"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[page.url for page in invalid_schema],
            evidence=[{"url": page.url, "errors": page.schema_errors} for page in invalid_schema],
        ))

    low_performance = [
        result for result in snapshot.lighthouse
        if result.available and result.performance is not None and result.performance < 50
    ]
    if low_performance:
        rule = rules["lighthouse_low_performance"]
        findings.append(_finding(
            rule,
            suffix="pages",
            urls=[result.url for result in low_performance],
            evidence=[{
                "url": result.url,
                "performance": result.performance,
                "lcp_ms": result.lcp_ms,
                "cls": result.cls,
                "tbt_ms": result.tbt_ms,
                "data_type": "Lighthouse laboratory",
            } for result in low_performance],
        ))

    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    findings.sort(key=lambda item: (priority_order.get(item.priority, 9), item.rule_id))
    return findings


def site_statistics(snapshot: SiteAuditSnapshot, findings: list[AuditFinding]) -> dict[str, Any]:
    """生成报告和 LLM 使用的紧凑站点统计。"""
    pages = snapshot.pages
    return {
        "discovered_urls": len(snapshot.discovered_urls),
        "crawled_pages": len(pages),
        "successful_html_pages": sum(
            page.status_code is not None and 200 <= page.status_code < 300 and "html" in page.content_type.lower()
            for page in pages
        ),
        "crawl_failures": sum(bool(page.error) for page in pages),
        "http_error_pages": sum(page.status_code is not None and page.status_code >= 400 for page in pages),
        "sitemap_page_urls": sum(page.in_sitemap for page in pages),
        "rule_findings": len(findings),
        "priority_counts": {
            priority: sum(item.priority == priority for item in findings) for priority in ("P0", "P1", "P2")
        },
        "lighthouse_attempts": len(snapshot.lighthouse),
        "lighthouse_successes": sum(item.available for item in snapshot.lighthouse),
    }
