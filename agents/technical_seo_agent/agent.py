"""编排技术事实、知识库规则、LLM 总结与最终输出校验。"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from core.llm import JSONLLM
from tools.progress import ProgressReporter

from .models import SiteAuditSnapshot, TechnicalAuditInput, TechnicalAuditOutput
from .prompts import AUDIT_SYSTEM, TRIAGE_SYSTEM
from .rules import load_rules, match_rules, site_statistics


class TechnicalSEOAgent:
    """技术审计主流程：规则引擎先确认事实，LLM 只负责整理和排序。"""

    def __init__(self, llm: JSONLLM, *, model_name: str = "unknown") -> None:
        """注入可替换 JSON 模型，便于真实供应商和 Mock 共用流程。"""
        self.llm = llm
        self.model_name = model_name

    def run(
        self,
        request: TechnicalAuditInput,
        snapshot: SiteAuditSnapshot,
        *,
        progress: ProgressReporter | None = None,
    ) -> TechnicalAuditOutput:
        """匹配规则、调用 LLM 整理、校验引用，并返回完整审计报告。"""
        progress = progress or ProgressReporter()
        progress.started("technical.rules", "匹配审计规则", "Python 正在对照技术 SEO 知识库")
        version, rules = load_rules()
        findings = match_rules(snapshot, request, rules)
        statistics = site_statistics(snapshot, findings)
        progress.completed(
            "technical.rules", "匹配审计规则", f"命中 {len(findings)} 组可追溯问题"
        )

        base_payload = {
            "domain": snapshot.root_url,
            "audit_goal": request.audit_goal or "（未提供）",
            "business_material": request.business_text[:20_000] or "（未提供）",
            "keyword_competitor_context": request.search_context[:12_000] or "（未提供）",
            "core_urls": request.core_urls,
            "statistics": statistics,
            "coverage_notes": snapshot.coverage_notes,
            "findings": [asdict(finding) for finding in findings],
        }
        progress.started("technical.triage", "归并技术问题", "LLM 第一阶段正在识别同类问题与修复依赖")
        triage = self.llm.chat_json(
            TRIAGE_SYSTEM,
            json.dumps({"findings": base_payload["findings"]}, ensure_ascii=False),
            name="technical_seo_triage",
            temperature=0.0,
        ) if findings else {"groups": []}
        expected = {finding.finding_id for finding in findings}
        issue_groups, triage_warnings = _validate_groups(triage.get("groups", []), expected)
        progress.completed("technical.triage", "归并技术问题", f"形成 {len(issue_groups)} 个问题组")

        progress.started("technical.summarize", "整理技术审计", "LLM 第二阶段正在生成说明与修复顺序")
        report_payload = {**base_payload, "issue_groups": issue_groups}
        analysis = self.llm.chat_json(
            AUDIT_SYSTEM,
            json.dumps(report_payload, ensure_ascii=False),
            name="technical_seo_audit",
            temperature=0.1,
        ) if findings else {
            "summary": "本次规则范围内未命中明确问题；仍需结合覆盖限制和搜索平台数据复核。",
            "ordered_finding_ids": [],
            "next_steps": ["查看抓取覆盖范围，并确认是否需要扩大页面数量。"],
        }
        progress.completed("technical.summarize", "整理技术审计", "LLM 审计整理完成")

        raw_order = analysis.get("ordered_finding_ids", [])
        valid_order: list[str] = []
        warnings: list[str] = list(triage_warnings)
        if isinstance(raw_order, list):
            for value in raw_order:
                finding_id = str(value).strip()
                if finding_id in expected and finding_id not in valid_order:
                    valid_order.append(finding_id)
                elif finding_id and finding_id not in expected:
                    warnings.append(f"LLM 返回了不存在的 finding_id，已丢弃：{finding_id}")
        missing = [finding.finding_id for finding in findings if finding.finding_id not in valid_order]
        if missing:
            warnings.append(f"LLM 漏排 {len(missing)} 个已确认问题，程序已按规则顺序补回。")
            valid_order.extend(missing)
        by_id = {finding.finding_id: finding for finding in findings}
        ordered = [by_id[finding_id] for finding_id in valid_order]
        next_steps = analysis.get("next_steps", [])
        if not isinstance(next_steps, list):
            next_steps = []
        summary, clean_steps, narrative_warnings = _validate_narrative(
            str(analysis.get("summary", "")).strip(),
            [str(item).strip() for item in next_steps if str(item).strip()][:6],
            statistics,
        )
        warnings.extend(narrative_warnings)
        limitations = [
            *snapshot.coverage_notes,
            "未提供百度搜索资源平台、GSC 或服务器日志时，本报告不判断实际收录、曝光、排名和蜘蛛访问。",
            "规则引用说明通用技术标准，不代表官方机构确认了该客户网站的具体问题。",
        ]
        return TechnicalAuditOutput(
            domain=snapshot.root_url,
            audit_goal=request.audit_goal,
            source_files=request.source_files,
            model=self.model_name,
            rules_version=version,
            summary=summary,
            issue_groups=issue_groups,
            findings=ordered,
            statistics=statistics,
            limitations=list(dict.fromkeys(limitations)),
            next_steps=clean_steps,
            validation_warnings=warnings,
        )


class MockTechnicalSEOAuditLLM:
    """离线测试使用的审计整理模型，不产生任何外部请求。"""

    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.1
    ) -> dict[str, Any]:
        """按 Python 已确认的问题顺序返回简短 Mock 摘要。"""
        del system, temperature
        payload = json.loads(user)
        finding_ids = [item["finding_id"] for item in payload.get("findings", [])]
        if name == "technical_seo_triage":
            return {
                "groups": [{
                    "group_name": "Mock 问题组",
                    "finding_ids": finding_ids,
                    "dependency_note": "Mock 模式不判断真实依赖。",
                }] if finding_ids else []
            }
        return {
            "summary": f"Mock 审计处理了 {len(finding_ids)} 组规则命中；不代表真实网站结论。",
            "ordered_finding_ids": finding_ids,
            "next_steps": ["关闭 Mock 模式后重新运行真实抓取与模型总结。"],
        }


def _validate_groups(raw_groups: Any, expected: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """校验第一阶段分组，丢弃虚构引用并把遗漏 finding 补回独立组。"""
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    warnings: list[str] = []
    if isinstance(raw_groups, list):
        for index, raw in enumerate(raw_groups, 1):
            if not isinstance(raw, dict):
                continue
            valid_ids: list[str] = []
            for value in raw.get("finding_ids", []):
                finding_id = str(value).strip()
                if finding_id in expected and finding_id not in seen:
                    valid_ids.append(finding_id)
                    seen.add(finding_id)
                elif finding_id and finding_id not in expected:
                    warnings.append(f"LLM 分组引用不存在的 finding_id，已丢弃：{finding_id}")
            if valid_ids:
                groups.append({
                    "group_name": str(raw.get("group_name", "")).strip() or f"问题组 {index}",
                    "finding_ids": valid_ids,
                    "dependency_note": str(raw.get("dependency_note", "")).strip(),
                })
    missing = sorted(expected - seen)
    if missing:
        warnings.append(f"LLM 分组漏掉 {len(missing)} 个问题，程序已补为独立问题组。")
        groups.extend({
            "group_name": "未归并问题",
            "finding_ids": [finding_id],
            "dependency_note": "",
        } for finding_id in missing)
    return groups, warnings


def _validate_narrative(
    summary: str,
    next_steps: list[str],
    statistics: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    """阻止模型在无平台数据时输出收录、排名、流量等无证据断言。"""
    forbidden = (
        "百度已收录", "百度未收录", "未被百度收录", "GSC显示", "GSC 显示",
        "百度站长显示", "搜索量为", "曝光量为", "点击量为", "排名为",
        "CrUX显示", "CrUX 显示", "真实用户数据表明",
    )

    def unsafe(text: str) -> bool:
        """判断模型文案是否包含当前流程无法证明的具体平台断言。"""
        compact = text.replace("：", "").replace(":", "")
        return any(marker in compact for marker in forbidden)

    warnings: list[str] = []
    if unsafe(summary):
        warnings.append("LLM 摘要包含无平台数据支持的断言，已替换为程序摘要。")
        counts = statistics.get("priority_counts", {})
        summary = (
            f"本次抓取 {statistics.get('crawled_pages', 0)} 个页面，"
            f"规则引擎命中 P0 {counts.get('P0', 0)}、P1 {counts.get('P1', 0)}、"
            f"P2 {counts.get('P2', 0)} 组问题；结论仅代表本次公共网站检测范围。"
        )
    clean_steps: list[str] = []
    for step in next_steps:
        if unsafe(step):
            warnings.append(f"LLM 下一步包含无数据支持的断言，已丢弃：{step[:80]}")
        else:
            clean_steps.append(step)
    return summary, clean_steps, warnings
