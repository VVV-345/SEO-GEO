"""关键词 Agent（v1：候选模式，LLM-only）。

职责：种子词 + 客户业务资料 + 已有页面 → P1/P2/P3 候选关键词清单。

设计要点：
- 不接真实搜索量/难度数据源；输出明确标注为「候选关键词」。
- 排序基于 LLM 对「商业意图 + 长尾具体度 + SERP 竞争估计」的判断（估计值，非数据）。
- 核心流程不硬依赖 openai 包（llm 为鸭子类型），方便单测注入假客户端。
- 两次 LLM 调用：扩展（生成候选）→ 打分（三维度）；中间为确定性的过滤/去重/分级。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class Candidate:
    keyword: str
    intent: str        # commercial | informational | navigational
    relevance: str     # high | medium | low
    note: str


@dataclass
class KeywordResult:
    keyword: str
    intent: str
    relevance: str
    commercial_intent: int
    specificity: int
    serp_difficulty: int
    composite: int
    tier: str          # P1 | P2 | P3
    rationale: str


@dataclass
class AgentResult:
    seed: str
    mode: str
    model: str
    num_requested: int
    candidates_raw: int
    candidates_after_filter: int
    items: list[KeywordResult] = field(default_factory=list)
    generated_at: str = ""


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不依赖 LLM）
# --------------------------------------------------------------------------- #

_PUNCT = "。.，,？?！!、；;：:"


def normalize_keyword(kw: str) -> str:
    """归一化：去首尾空白、折叠内部多空白、去掉首尾常见标点。"""
    kw = re.sub(r"\s+", " ", kw.strip())
    while kw and kw[-1] in _PUNCT:
        kw = kw[:-1].rstrip()
    while kw and kw[0] in _PUNCT:
        kw = kw[1:].lstrip()
    return kw


def _dedupe_key(kw: str) -> str:
    """去重键：归一化后再去掉所有空白。

    中文查询里的空格往往是输入噪音（「企业知识库 私有化部署」≈「企业知识库私有化部署」），
    按去空格后的形式合并可避免这类近重复漏网。
    """
    return re.sub(r"\s+", "", normalize_keyword(kw))


def composite_score(commercial_intent: int, specificity: int, serp_difficulty: int) -> int:
    """综合分 = 商业意图 + 长尾具体度 + (6 − SERP难度)。区间 [3, 15]，越高越优先。"""
    return commercial_intent + specificity + (6 - serp_difficulty)


def assign_tier(score: int, relevance: str) -> str:
    if relevance == "low":
        return "P3"
    if score >= 11:
        return "P1"
    if score >= 8:
        return "P2"
    return "P3"


def filter_by_relevance(
    candidates: list[Candidate], keep: tuple[str, ...] = ("high", "medium")
) -> list[Candidate]:
    """丢弃与客户业务低相关的词。"""
    return [c for c in candidates if c.relevance in keep]


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """按去重键合并近重复，保留先出现的。"""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = _dedupe_key(c.keyword)
        if not key or key in seen:
            continue
        seen.add(key)
        norm = normalize_keyword(c.keyword)
        out.append(Candidate(keyword=norm, intent=c.intent, relevance=c.relevance, note=c.note))
    return out


# --------------------------------------------------------------------------- #
# LLM 接口（鸭子类型；真实实现见 llm.py）
# --------------------------------------------------------------------------- #

class LLM(Protocol):
    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.5
    ) -> dict[str, Any]:
        ...


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

EXPAND_SYSTEM = """你是一名资深的中文 SEO / GEO 关键词策略师。

任务：根据「种子词 + 客户业务资料 + 已有页面」，扩展出最多 N 个**长尾候选关键词**，供后续筛选值得做的页面主题。

硬性要求：
1. 只产出长尾词（通常 4 字以上、带具体场景或明确意图的查询），不要泛词。
2. 每个词必须与客户**真实业务**相关——依据客户业务资料判断，不要臆造客户没有的业务线。
3. 意图混合：commercial（购买/转化：方案、报价、选型、对比、采购、私有化、厂商）、informational（科普：是什么、怎么做、教程、区别）、少量 navigational（导航：某产品官网）。
4. 去重：不要出现语义或字面近重复的词。
5. 为每个词标注与客户业务的相关性（high/medium/low）和一句话说明。
6. **严禁编造搜索量、难度、指数等数据**——本版只产出候选。

只输出 JSON：{"candidates": [{"keyword": "...", "intent": "commercial|informational|navigational", "relevance": "high|medium|low", "note": "一句话相关性说明"}]}
"""


RANK_SYSTEM = """你是一名中文 SEO 关键词排序专家。当前**没有**真实搜索量/难度数据，请基于经验与语义给出估计，并明确这是估计值。

对每个候选词按三个维度打分（1-5 整数）：
- commercial_intent 商业意图：越接近购买/转化越高（5=明确采购意向，1=纯科普）。
- specificity 长尾具体度：越具体、越聚焦细分场景越高（5=极具体，1=泛词）。
- serp_difficulty SERP 竞争估计：依据「百度/Bing 该词前 10 通常是谁在排」的经验估计（5=大站/百科/强品牌垄断，极难；1=弱站居多，较易）。这是估计，不是真实数据。

同时为每个词写一句话排序理由（结合三维度；若已被「已有页面」覆盖，应在难度或理由中点出 cannibalization 风险）。

只输出 JSON：{"ranked": [{"keyword": "...", "intent": "...", "commercial_intent": 1-5, "specificity": 1-5, "serp_difficulty": 1-5, "rationale": "..."}]}
要求：关键词保持原样，不要改写、增删或合并。
"""


def expand_user(seeds: list[str], business_text: str, pages_text: str, num: int) -> str:
    return (
        f"种子词：{', '.join(seeds)}\n"
        f"候选数量上限：{num}\n\n"
        f"客户业务资料：\n{business_text.strip() or '（未提供）'}\n\n"
        f"客户已有页面（避免重复造轮子）：\n{pages_text.strip() or '（未提供）'}\n\n"
        "请扩展候选关键词，严格按指定 JSON 输出。"
    )


def rank_user(candidates: list[Candidate], business_text: str, pages_text: str) -> str:
    lines = "\n".join(
        f"- {c.keyword}（意图={c.intent}，相关性={c.relevance}）" for c in candidates
    )
    return (
        "候选词清单：\n" + lines + "\n\n"
        f"客户业务资料：\n{business_text.strip() or '（未提供）'}\n\n"
        f"客户已有页面：\n{pages_text.strip() or '（未提供）'}\n\n"
        "请按三维度打分并排序，严格按指定 JSON 输出，关键词保持原样。"
    )


# --------------------------------------------------------------------------- #
# 解析（防御性：LLM 偶尔会返回略偏的 schema）
# --------------------------------------------------------------------------- #

def _clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, x))


def _parse_candidates(data: dict[str, Any]) -> list[Candidate]:
    out: list[Candidate] = []
    for raw in data.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        kw = str(raw.get("keyword", "")).strip()
        if not kw:
            continue
        out.append(
            Candidate(
                keyword=kw,
                intent=str(raw.get("intent", "informational")).strip().lower(),
                relevance=str(raw.get("relevance", "medium")).strip().lower(),
                note=str(raw.get("note", "")).strip(),
            )
        )
    return out


def _parse_ranked(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in data.get("ranked", []):
        if not isinstance(raw, dict):
            continue
        kw = str(raw.get("keyword", "")).strip()
        if not kw:
            continue
        out.append(
            {
                "keyword": kw,
                "intent": str(raw.get("intent", "informational")).strip().lower(),
                "commercial_intent": _clamp_int(raw.get("commercial_intent"), 1, 5),
                "specificity": _clamp_int(raw.get("specificity"), 1, 5),
                "serp_difficulty": _clamp_int(raw.get("serp_difficulty"), 1, 5),
                "rationale": str(raw.get("rationale", "")).strip(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def run_keyword_agent(
    seeds: list[str],
    business_text: str,
    existing_pages: list[dict],
    llm: LLM,
    *,
    num: int = 50,
    model_name: str = "unknown",
) -> AgentResult:
    pages_text = _format_pages(existing_pages)

    # 1) 扩展：种子词 + 业务 + 已有页面 → 候选词（含意图/相关性）
    expand_data = llm.chat_json(
        EXPAND_SYSTEM, expand_user(seeds, business_text, pages_text, num),
        name="expand", temperature=0.8,
    )
    raw_candidates = _parse_candidates(expand_data)
    raw_count = len(raw_candidates)

    # 2) 业务相关性过滤 + 去重
    filtered = filter_by_relevance(raw_candidates)
    deduped = dedupe_candidates(filtered)

    # 3) 打分：候选词 → 三维度评分 + 理由
    rank_data = llm.chat_json(
        RANK_SYSTEM, rank_user(deduped, business_text, pages_text),
        name="rank", temperature=0.3,
    )
    ranked = _parse_ranked(rank_data)

    # 4) 合并 → 综合分 → 分级
    rel_by_kw = {_dedupe_key(c.keyword): c.relevance for c in deduped}
    intent_by_kw = {_dedupe_key(c.keyword): c.intent for c in deduped}
    items: list[KeywordResult] = []
    for r in ranked:
        key = _dedupe_key(r["keyword"])
        relevance = rel_by_kw.get(key, "medium")
        intent = r.get("intent") or intent_by_kw.get(key, "informational")
        comp = composite_score(r["commercial_intent"], r["specificity"], r["serp_difficulty"])
        items.append(
            KeywordResult(
                keyword=r["keyword"],
                intent=intent,
                relevance=relevance,
                commercial_intent=r["commercial_intent"],
                specificity=r["specificity"],
                serp_difficulty=r["serp_difficulty"],
                composite=comp,
                tier=assign_tier(comp, relevance),
                rationale=r["rationale"],
            )
        )

    # 5) 排序：P1 → P2 → P3，组内按综合分降序
    tier_order = {"P1": 0, "P2": 1, "P3": 2}
    items.sort(key=lambda it: (tier_order.get(it.tier, 9), -it.composite))

    return AgentResult(
        seed=", ".join(seeds),
        mode="candidate-llm-only",
        model=model_name,
        num_requested=num,
        candidates_raw=raw_count,
        candidates_after_filter=len(deduped),
        items=items,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _format_pages(pages: list[dict]) -> str:
    if not pages:
        return ""
    lines: list[str] = []
    for p in pages:
        title = str(p.get("title", "")).strip()
        kw = str(p.get("target_keyword", "")).strip()
        if title and kw:
            lines.append(f"- {title}（目标词：{kw}）")
        elif title:
            lines.append(f"- {title}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #

def write_json(result: AgentResult, path: Path) -> None:
    payload = {
        "seed": result.seed,
        "mode": result.mode,
        "model": result.model,
        "generated_at": result.generated_at,
        "num_requested": result.num_requested,
        "candidates_raw": result.candidates_raw,
        "candidates_after_filter": result.candidates_after_filter,
        "note": "候选模式：不含真实搜索量/难度数据，排序基于 LLM 估计。"
        "接入 5118/百度等数据源后可输出带真实指标的 P1/P2/P3。",
        "scoring": "composite = commercial_intent + specificity + (6 - serp_difficulty); P1>=11, P2 8-10, P3<=7",
        "items": [it.__dict__ for it in result.items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(result: AgentResult, path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# 关键词候选清单 — {result.seed}")
    lines.append("")
    lines.append(
        f"> 模式：**候选模式（candidate-llm-only）** ｜ 模型：{result.model} ｜ "
        f"生成时间：{result.generated_at}"
    )
    lines.append(">")
    lines.append(
        "> ⚠️ 本清单**不含真实搜索量/难度数据**。排序基于 LLM 对「商业意图 + 长尾具体度 + "
        "SERP 竞争估计」的判断（估计值）。"
    )
    lines.append("> 要得到带真实数据的 P1/P2/P3，需后续接入 5118 / 百度推广 / 百度指数（接口已预留）。")
    lines.append("")
    lines.append(f"- 候选数：{result.candidates_raw} → 过滤去重后 {result.candidates_after_filter}")
    lines.append("- 排序公式：`composite = 商业意图 + 长尾具体度 + (6 − SERP难度)`，区间 [3,15]")
    lines.append("- 分级：**P1 ≥ 11** ｜ **P2 8–10** ｜ **P3 ≤ 7**（相关性 low 一律降为 P3）")
    lines.append("")

    for tier in ("P1", "P2", "P3"):
        tier_items = [it for it in result.items if it.tier == tier]
        lines.append(f"## {tier}（{len(tier_items)}）")
        if not tier_items:
            lines.append("_（无）_")
            lines.append("")
            continue
        for it in tier_items:  # 速览，匹配 spec 的输出形态
            lines.append(f"- {it.keyword}")
        lines.append("")
        lines.append("| 关键词 | 意图 | 相关性 | 商业 | 具体度 | 难度 | 综合 | 理由 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for it in tier_items:
            lines.append(
                f"| {it.keyword} | {it.intent} | {it.relevance} | {it.commercial_intent} | "
                f"{it.specificity} | {it.serp_difficulty} | {it.composite} | {it.rationale} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Mock（无 API key 时预览流程 / 单测可复用）
# --------------------------------------------------------------------------- #

class MockLLM:
    """返回写死响应，便于无 API key 时预览流程与输出形态。"""

    def chat_json(
        self, system: str, user: str, *, name: str = "call", temperature: float = 0.5
    ) -> dict[str, Any]:
        if name == "expand":
            return {"candidates": [
                {"keyword": "企业知识库私有化部署", "intent": "commercial", "relevance": "high", "note": "核心售卖场景"},
                {"keyword": "制造业企业知识库解决方案", "intent": "commercial", "relevance": "high", "note": "目标行业"},
                {"keyword": "企业知识库 信创 国产化", "intent": "commercial", "relevance": "high", "note": "政企合规卖点"},
                {"keyword": "Dify 企业知识库搭建教程", "intent": "informational", "relevance": "medium", "note": "竞品/开源科普"},
                {"keyword": "免费企业知识库软件", "intent": "commercial", "relevance": "medium", "note": "价格敏感长尾"},
                {"keyword": "免费个人笔记软件", "intent": "commercial", "relevance": "low", "note": "客户不做 C 端，应被过滤"},
            ]}
        return {"ranked": [
            {"keyword": "企业知识库私有化部署", "intent": "commercial", "commercial_intent": 5, "specificity": 4, "serp_difficulty": 3, "rationale": "高购买意向 + 较具体 + 中等竞争，优先做"},
            {"keyword": "制造业企业知识库解决方案", "intent": "commercial", "commercial_intent": 5, "specificity": 5, "serp_difficulty": 3, "rationale": "行业垂直、意向强、竞争适中"},
            {"keyword": "企业知识库 信创 国产化", "intent": "commercial", "commercial_intent": 5, "specificity": 4, "serp_difficulty": 2, "rationale": "政企刚需、竞争较弱"},
            {"keyword": "Dify 企业知识库搭建教程", "intent": "informational", "commercial_intent": 2, "specificity": 4, "serp_difficulty": 3, "rationale": "科普意图，引流价值中等"},
            {"keyword": "免费企业知识库软件", "intent": "commercial", "commercial_intent": 3, "specificity": 3, "serp_difficulty": 4, "rationale": "价格词竞争激烈、转化一般"},
        ]}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _slug(s: str) -> str:
    s = re.sub(r'[\s/\\:*?"<>|]+', "_", s).strip("_")
    return s[:40] or "seed"


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，强制 stdout/stderr 走 UTF-8，避免打印中文/符号时编码报错
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="keyword_agent", description="关键词 Agent v1（候选模式）")
    p.add_argument("--seed", nargs="+", required=True, help="种子词（可多个）")
    p.add_argument("--business", help="客户业务资料文件路径（markdown/txt）")
    p.add_argument("--pages", help="已有页面 JSON：{pages:[{title,target_keyword}]}")
    p.add_argument("--num", type=int, default=50, help="候选词数量上限（默认 50）")
    p.add_argument("-o", "--output-dir", default="output", help="输出目录（默认 output）")
    p.add_argument("--mock", action="store_true", help="用写死响应跑流程（无需 API key，用于预览）")
    args = p.parse_args(argv)

    business_text = Path(args.business).read_text(encoding="utf-8") if args.business else ""

    existing_pages: list[dict] = []
    if args.pages:
        data = json.loads(Path(args.pages).read_text(encoding="utf-8"))
        existing_pages = list(data.get("pages", [])) if isinstance(data, dict) else []

    if args.mock:
        llm: Any = MockLLM()
        model_name = "mock"
    else:
        from .config import load_llm_config
        from .llm import OpenAILLM

        cfg = load_llm_config()
        llm = OpenAILLM(cfg)
        model_name = cfg.model

    result = run_keyword_agent(
        seeds=args.seed,
        business_text=business_text,
        existing_pages=existing_pages,
        llm=llm,
        num=args.num,
        model_name=model_name,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _slug("_".join(args.seed))
    json_path = out_dir / f"keywords_{base}.json"
    md_path = out_dir / f"keywords_{base}.md"
    write_json(result, json_path)
    write_markdown(result, md_path)

    p1 = sum(1 for it in result.items if it.tier == "P1")
    p2 = sum(1 for it in result.items if it.tier == "P2")
    p3 = sum(1 for it in result.items if it.tier == "P3")
    print(f"✓ 完成（{result.candidates_raw} 候选 → {result.candidates_after_filter} 去重过滤后）")
    print(f"  P1={p1}  P2={p2}  P3={p3}")
    print(f"  JSON: {json_path}")
    print(f"  MD  : {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
