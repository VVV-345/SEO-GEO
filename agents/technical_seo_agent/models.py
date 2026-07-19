"""技术 SEO 审计 Agent 的输入、抓取事实和报告数据模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TechnicalAuditInput:
    """一次公共网站技术审计的输入；只有域名是必填项。"""

    domain: str
    audit_goal: str = ""
    business_text: str = ""
    search_context: str = ""
    source_files: list[str] = field(default_factory=list)
    core_urls: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    max_pages: int = 50
    run_lighthouse: bool = True
    lighthouse_limit: int = 3


@dataclass(frozen=True)
class LighthouseResult:
    """单页 Lighthouse 实验室检测结果；不可冒充真实用户数据。"""

    url: str
    available: bool
    performance: int | None = None
    seo: int | None = None
    accessibility: int | None = None
    best_practices: int | None = None
    lcp_ms: float | None = None
    cls: float | None = None
    tbt_ms: float | None = None
    error: str = ""


@dataclass(frozen=True)
class AuditPage:
    """一个站内 URL 的可复核技术事实快照。"""

    url: str
    final_url: str = ""
    status_code: int | None = None
    content_type: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    title: str = ""
    meta_description: str = ""
    h1: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    canonical: str = ""
    robots_meta: str = ""
    schema_types: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    image_count: int = 0
    missing_alt_count: int = 0
    word_count: int = 0
    html_size: int = 0
    response_time_ms: int | None = None
    in_sitemap: bool = False
    robots_allowed: bool | None = None
    error: str = ""


@dataclass(frozen=True)
class SiteAuditSnapshot:
    """工具层产生的整站事实，尚未经过规则或 LLM 判断。"""

    root_url: str
    robots_url: str
    robots_status: int | None
    robots_text: str
    robots_error: str
    sitemap_urls: list[str]
    sitemap_statuses: dict[str, int | None]
    sitemap_errors: list[str]
    discovered_urls: list[str]
    pages: list[AuditPage]
    inlink_counts: dict[str, int]
    crawl_errors: list[str]
    lighthouse: list[LighthouseResult]
    coverage_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        """把快照递归序列化为可保存和传给下游 Agent 的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class RuleReference:
    """规则的公开依据；checked_at 表示本项目最后核对日期。"""

    source: str
    title: str
    url: str
    checked_at: str


@dataclass(frozen=True)
class AuditRule:
    """从本地知识库读取的可维护技术审计规则卡片。"""

    rule_id: str
    category: str
    title: str
    default_priority: str
    confidence: str
    impact: str
    fix: str
    validation: str
    scope: str
    limitations: list[str]
    references: list[RuleReference]


@dataclass(frozen=True)
class AuditFinding:
    """Python 规则引擎命中的问题及其原始证据。"""

    finding_id: str
    rule_id: str
    category: str
    priority: str
    confidence: str
    title: str
    affected_urls: list[str]
    evidence: list[dict[str, Any]]
    impact: str
    fix: str
    validation: str
    references: list[RuleReference]
    business_reason: str = ""


@dataclass(frozen=True)
class TechnicalAuditOutput:
    """经规则匹配、LLM 排序和程序校验后的最终审计结果。"""

    domain: str
    audit_goal: str
    source_files: list[str]
    model: str
    rules_version: str
    summary: str
    issue_groups: list[dict[str, Any]]
    findings: list[AuditFinding]
    statistics: dict[str, Any]
    limitations: list[str]
    next_steps: list[str]
    validation_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """序列化完整报告，并保留各字段的使用说明。"""
        payload = asdict(self)
        payload["_field_descriptions"] = {
            "findings": "每项必须能追溯到本次抓取事实和规则库 rule_id。",
            "priority": "P0/P1/P2；P0 仅用于阻断核心抓取、索引或严重全站故障。",
            "confidence": "confirmed、likely 或 manual_review。",
            "references": "规则依据，不代表官方确认了该客户网站的具体问题。",
            "limitations": "本次数据覆盖和无法自动确认的边界。",
        }
        return payload
