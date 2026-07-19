"""可解释的 SERP 竞争估算规则。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from tools.baidu_serp import SearchResult

from .models import CompetitionEvidence


AUTHORITY_DOMAINS = {
    "baidu.com", "baike.baidu.com", "zhihu.com", "csdn.net", "qq.com", "163.com",
    "sohu.com", "sina.com.cn", "bilibili.com", "douyin.com", "jd.com", "tmall.com",
    "taobao.com", "36kr.com", "thepaper.cn", "people.com.cn", "gov.cn",
}
# 这是透明、可维护的启发式名单，并非“官方权威站点”判定；可随目标市场补充。


def _compact(text: str) -> str:
    """仅供标题覆盖比对，忽略中文查询中常见的空白与标点差异。"""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def _is_authority(domain: str) -> bool:
    """判断域名是否命中可维护的强势平台启发式名单。"""
    domain = domain.lower().removeprefix("www.")
    return any(domain == item or domain.endswith("." + item) for item in AUTHORITY_DOMAINS)


def _is_homepage(url: str) -> bool:
    """根据 URL 路径判断结果是否指向站点首页。"""
    path = urlparse(url).path.strip("/")
    return not path or path.lower() in {"index.html", "index.htm", "index.php"}


def estimate_competition(keyword: str, results: list[SearchResult]) -> CompetitionEvidence:
    """0-100，越高表示当前 SERP 越难；不足 5 条时证据不足。"""
    if not results:
        return CompetitionEvidence(50, "unknown", 0, 0, 0, 0, ["未获取到百度自然结果，无法可靠估算"])

    count = len(results)
    compact_keyword = _compact(keyword)
    exact_titles = sum(compact_keyword in _compact(item.title) for item in results)
    authorities = sum(_is_authority(item.domain) for item in results if item.domain)
    homepages = sum(_is_homepage(item.url) for item in results)
    domains = {item.domain for item in results if item.domain}

    exact_ratio = exact_titles / count
    authority_ratio = authorities / count
    homepage_ratio = homepages / count
    unique_ratio = len(domains) / count if domains else 0

    # 标题精确覆盖说明结果针对性强；权威域名和首页说明竞争主体强；
    # 域名重复说明少数站点占据多个位置。四项之和为 100。
    # 这是 SERP 快照的相对估算，不能替代搜索量或第三方“关键词难度”。
    score = round(
        exact_ratio * 35
        + authority_ratio * 35
        + homepage_ratio * 15
        + (1 - unique_ratio) * 15
    )
    score = max(0, min(100, score))
    if count < 5:
        level = "unknown"
    elif score >= 65:
        level = "high"
    elif score >= 35:
        level = "medium"
    else:
        level = "low"

    evidence = [
        f"前 {count} 条中 {exact_titles} 条标题直接覆盖关键词",
        f"强势平台/权威域名 {authorities} 条",
        f"首页结果 {homepages} 条",
        f"独立域名 {len(domains)} 个",
    ]
    if count < 5:
        evidence.append("自然结果少于 5 条，本次竞争等级标记为 unknown")
    return CompetitionEvidence(
        score, level, round(exact_ratio, 2), round(authority_ratio, 2),
        round(homepage_ratio, 2), round(unique_ratio, 2), evidence,
    )


def opportunity_score(business_fit: int, commercial: int, specificity: int, competition: CompetitionEvidence) -> int:
    """业务价值占 70%，SERP 可进入性占 30%；unknown 按中性 50 处理。"""
    business = (business_fit * 8) + (commercial * 4) + (specificity * 2)  # 最高 70
    competition_score = 50 if competition.level == "unknown" else competition.score
    return max(0, min(100, round(business + (100 - competition_score) * 0.30)))


def priority(score: int, business_fit: int, competition_level: str) -> str:
    """返回业务优先级；SERP 证据不足时只允许进入“待验证”队列。"""
    if competition_level == "unknown":
        return "待验证"
    if score >= 72 and business_fit >= 4:
        return "P1"
    if score >= 52 and business_fit >= 3:
        return "P2"
    return "P3"
