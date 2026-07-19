"""百度搜索结果、相关搜索与下拉联想采集。

这是网页快照采集，不是官方搜索量或关键词难度数据源。百度结构或访问策略变化时，
调用方应把结果标记为不完整，而不是补造数据。
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .webpage import DEFAULT_HEADERS


# 百度站点内导航请求头：在通用浏览器头基础上补 Referer 与 Sec-Fetch-*，
# 让 /link 跳转看起来像从百度结果页发起的真实点击。
BAIDU_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://www.baidu.com/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}


@dataclass(frozen=True)
class SearchResult:
    rank: int
    title: str
    url: str
    domain: str
    snippet: str = ""


@dataclass(frozen=True)
class FilteredSearchResult:
    """未进入竞品分析的 SERP 结果及其可审计过滤原因。"""

    rank: int
    title: str
    url: str
    domain: str
    reason: str


@dataclass(frozen=True)
class BaiduSERP:
    keyword: str
    suggestions: list[str] = field(default_factory=list)
    related_searches: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    complete: bool = False
    error: str = ""
    filtered_results: list[FilteredSearchResult] = field(default_factory=list)


class SERPFallback(Protocol):
    """浏览器等备用采集器的最小接口，便于注入测试或替换实现。"""

    def search_results(self, keyword: str, *, limit: int = 10) -> tuple[list[str], list[SearchResult]]:
        """返回相关搜索和自然结果，失败时由具体实现抛出可读异常。"""
        ...

    def close(self) -> None:
        """释放备用采集器持有的浏览器或网络资源。"""
        ...


class BaiduSERPClient:
    """轻量百度网页采集器；适用于研究快照，不应作为高频批量爬虫。"""

    def __init__(
        self,
        *,
        timeout: float = 12,
        delay: float = 3.0,
        resolve_top_n: int = 10,
        browser_fallback: SERPFallback | None = None,
    ):
        """配置超时、请求节奏、URL 解析上限和可选浏览器回退。"""
        self.timeout = timeout
        self.delay = delay
        # 只解析前 N 条的真实落地 URL，超出部分丢弃，控制 /link 跳转请求量。
        # 关键词 Agent 只需足够估算竞争度 + 预览 top URL；竞品 Agent 会自行抓取。
        self.resolve_top_n = resolve_top_n
        self.browser_fallback = browser_fallback
        self._prefer_browser = False
        self._browser_blocked = False
        self._warmed = False
        self.session = requests.Session()
        self.session.headers.update(BAIDU_HEADERS)

    def search(self, keyword: str, *, limit: int = 10) -> BaiduSERP:
        """按桌面 HTML、移动 HTML、浏览器顺序查询一个关键词。"""
        self._warm_up()
        suggestions: list[str] = []
        partial_errors: list[str] = []
        try:
            suggestions = self._suggest(keyword)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
            partial_errors.append(f"下拉词获取失败：{error}")

        # 同一批任务第一次已确认静态搜索不可用后，后续词直接复用浏览器上下文。
        if self._browser_blocked:
            partial_errors.append(
                "本批次浏览器已被百度安全验证拦截，已停止后续浏览器查询；请稍后重试或减少候选词数量"
            )
            return BaiduSERP(keyword=keyword, suggestions=suggestions, error="；".join(partial_errors))
        if self._prefer_browser and self.browser_fallback is not None:
            return self._search_with_browser(keyword, suggestions, limit=limit, prior_errors=partial_errors)
        try:
            # 下拉词失败不影响自然结果采集，因此两类请求独立容错。
            response = self.session.get(
                "https://www.baidu.com/s",
                params={"wd": keyword, "rn": limit, "ie": "utf-8"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            if is_baidu_verification_page(response.text):
                partial_errors.append("百度桌面搜索返回安全验证页")
                related, results = [], []
            else:
                related, results = parse_serp_html(response.text, base_url=response.url, limit=limit)

            # 移动搜索页通常是服务端渲染；桌面 DOM 变化或被简化时先用它做轻量回退。
            if not results:
                mobile_related, mobile_results, mobile_error = self._search_mobile(keyword, limit=limit)
                related = related or mobile_related
                results = mobile_results
                if mobile_error:
                    partial_errors.append(mobile_error)

            # requests 两条路径都失败后才启动浏览器，避免每个词都承担浏览器成本。
            if not results and self.browser_fallback is not None:
                try:
                    browser_related, browser_results = self.browser_fallback.search_results(keyword, limit=limit)
                    related = related or browser_related
                    results = browser_results
                    if results:
                        self._prefer_browser = True
                except Exception as error:
                    if "安全验证" in str(error):
                        self._browser_blocked = True
                    partial_errors.append(f"浏览器回退失败：{error}")
            results, filtered_results = self._finalize_results(results)
            if not results and filtered_results:
                reasons = list(dict.fromkeys(item.reason for item in filtered_results))
                partial_errors.append(
                    f"解析到 {len(filtered_results)} 条结果，但均被竞品 URL 规则过滤：{'、'.join(reasons)}"
                )
            if not results:
                # HTTP 200 不代表拿到了自然结果：百度可能返回验证页、广告页，或更换了 DOM。
                # 必须显式记录，避免上层把“没有解析到”误判为“这个词没有竞争”。
                partial_errors.append("百度自然结果页未解析到可用条目（可能是验证页、访问限制或页面结构变化）")
            if self.delay:
                # 固定间隔易被识别为脚本，主查询后用抖动延迟。
                time.sleep(random.uniform(self.delay, self.delay * 2.2))
            return BaiduSERP(
                keyword,
                suggestions,
                related,
                results,
                len(results) >= min(self.resolve_top_n, limit),
                "；".join(partial_errors),
                filtered_results,
            )
        except (requests.RequestException, ValueError) as error:
            partial_errors.append(f"自然结果获取失败：{error}")
            return BaiduSERP(keyword=keyword, suggestions=suggestions, error="；".join(partial_errors))

    def _search_with_browser(
        self,
        keyword: str,
        suggestions: list[str],
        *,
        limit: int,
        prior_errors: list[str],
    ) -> BaiduSERP:
        """复用已启动的浏览器直接读取下一关键词，减少重复失败请求。"""
        assert self.browser_fallback is not None
        errors = list(prior_errors)
        try:
            related, results = self.browser_fallback.search_results(keyword, limit=limit)
            results, filtered_results = self._finalize_results(results)
            if not results and filtered_results:
                reasons = list(dict.fromkeys(item.reason for item in filtered_results))
                errors.append(
                    f"解析到 {len(filtered_results)} 条结果，但均被竞品 URL 规则过滤：{'、'.join(reasons)}"
                )
            if not results:
                errors.append("浏览器页面未解析到百度自然结果")
            if self.delay:
                time.sleep(random.uniform(self.delay, self.delay * 2.2))
            return BaiduSERP(
                keyword,
                suggestions,
                related,
                results,
                len(results) >= min(self.resolve_top_n, limit),
                "；".join(errors),
                filtered_results,
            )
        except Exception as error:
            if "安全验证" in str(error):
                self._browser_blocked = True
            errors.append(f"浏览器回退失败：{error}")
            return BaiduSERP(keyword=keyword, suggestions=suggestions, error="；".join(errors))

    def close(self) -> None:
        """释放会话和可选浏览器；工作流结束时调用。"""
        self.session.close()
        if self.browser_fallback is not None:
            self.browser_fallback.close()

    def _warm_up(self) -> None:
        """首次查询前访问百度首页获取 BAIDUID 等会话 Cookie，只执行一次。"""
        if self._warmed:
            return
        self._warmed = True
        try:
            self.session.get("https://www.baidu.com/", timeout=self.timeout)
        except requests.RequestException:
            # 预热失败不致命：继续走正常采集，最坏只是少一份会话 Cookie。
            pass

    def _finalize_results(
        self, results: list[SearchResult]
    ) -> tuple[list[SearchResult], list[FilteredSearchResult]]:
        """解析并筛选适合抓取正文的落地页，同时保留过滤原因。

        先尝试解析百度中转链接；只有仍停留在中转/广告/搜索聚合页的结果才会
        被过滤。过滤不会伪造替代 URL，也不会把站外普通内容页静默删除。
        """
        accepted: list[SearchResult] = []
        filtered: list[FilteredSearchResult] = []
        for item in results:
            resolved = self._resolve_result(item)
            reason = competitor_url_rejection_reason(resolved.url)
            if reason:
                filtered.append(FilteredSearchResult(
                    resolved.rank, resolved.title, resolved.url, resolved.domain, reason
                ))
                continue
            accepted.append(resolved)
            if len(accepted) >= self.resolve_top_n:
                break
        return accepted, filtered

    def _search_mobile(self, keyword: str, *, limit: int) -> tuple[list[str], list[SearchResult], str]:
        """请求移动版百度，在桌面结构不可用时提供轻量回退。"""
        try:
            response = self.session.get(
                "https://m.baidu.com/s",
                params={"word": keyword, "rn": limit, "ie": "utf-8"},
                headers={
                    **BAIDU_HEADERS,
                    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/138.0.0.0 Mobile Safari/537.36",
                    "Sec-Ch-Ua-Mobile": "?1",
                    "Sec-Ch-Ua-Platform": '"Android"',
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            if is_baidu_verification_page(response.text):
                return [], [], "百度移动搜索返回安全验证页"
            related, results = parse_serp_html(response.text, base_url=response.url, limit=limit)
            if not results:
                return related, [], "百度移动搜索页也未解析到自然结果"
            return related, results, ""
        except requests.RequestException as error:
            return [], [], f"百度移动搜索请求失败：{error}"

    def _resolve_result(self, result: SearchResult) -> SearchResult:
        """尽量把百度中转链接解析为落地 URL；失败时保留原始链接。"""
        redirect_paths = ("/link", "/other.php", "/baidu.php")
        if (
            result.domain not in {"baidu.com", "www.baidu.com", "m.baidu.com"}
            or not any(path in urlparse(result.url).path for path in redirect_paths)
        ):
            return result
        if self.delay:
            # /link 跳转请求之间加随机抖动，避免连续无间隔的爆发式请求触发风控。
            time.sleep(random.uniform(0.25, 0.8))
        try:
            response = self.session.get(result.url, timeout=self.timeout, allow_redirects=True, stream=True)
            final_url = response.url
            response.close()
            domain = urlparse(final_url).netloc.lower().removeprefix("www.")
            if domain and domain != "baidu.com":
                return SearchResult(result.rank, result.title, final_url, domain, result.snippet)
        except requests.RequestException:
            pass
        return result

    def _suggest(self, keyword: str) -> list[str]:
        """读取百度下拉接口，兼容 JSON 与 JSONP 两种返回格式。"""
        response = self.session.get(
            "https://suggestion.baidu.com/su",
            params={"wd": keyword, "action": "opensearch"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        text = response.text.strip()
        try:
            data = response.json()
        except requests.JSONDecodeError:
            # 百度有时返回 JSONP，而非 application/json。
            match = re.search(r"\((.*)\)\s*;?$", text, re.S)
            data = json.loads(match.group(1)) if match else []
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            return [str(item).strip() for item in data[1] if str(item).strip()][:10]
        if isinstance(data, dict):
            return [str(item).strip() for item in data.get("s", []) if str(item).strip()][:10]
        return []


def competitor_url_rejection_reason(url: str) -> str:
    """判断 URL 是否明显不适合作为竞品正文，并返回中文原因。

    这里只排除可以确定的百度广告/中转/站内搜索页面和明确的付费搜索跟踪页；
    普通内容页即使之后可能返回 403，也仍保留给竞品抓取阶段处理。
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0].removeprefix("www.")
    path = parsed.path.lower().rstrip("/") or "/"
    query = parse_qs(parsed.query.lower())
    if parsed.scheme not in {"http", "https"} or not host:
        return "不是有效的 HTTP(S) 落地页"
    if host in {"ada.baidu.com", "e.baidu.com", "pos.baidu.com", "union.baidu.com"}:
        return "百度广告或推广服务页面"
    if host in {"baidu.com", "m.baidu.com"} and path in {"/other.php", "/link", "/baidu.php"}:
        return "百度中转链接未能解析为真实落地页"
    if host in {"baidu.com", "m.baidu.com"} and path in {"/s", "/search"}:
        return "百度站内搜索结果页，不是独立竞品正文"
    if host == "wenku.baidu.com" and path.startswith("/search"):
        return "百度文库搜索聚合页，不是独立文档正文"
    paid_mediums = {value for values in query.get("utm_medium", []) for value in values.split(",")}
    if paid_mediums & {"cpc", "ppc", "paid", "paidsearch", "paid_search"}:
        return "URL 带有明确的付费搜索跟踪参数"
    return ""


def is_baidu_verification_page(html: str) -> bool:
    """识别常见验证/风控页，避免把 HTTP 200 当成正常搜索结果。"""
    markers = ("安全验证", "请输入验证码", "百度安全验证", "网络不给力，请稍后重试", "wappass.baidu.com/static/captcha")
    return any(marker in html for marker in markers)


_CONTAINER_URL_ATTRS = ("mu", "data-landurl", "data-url", "data-land-url", "data-log-url")
_ANCHOR_URL_ATTRS = ("data-landurl", "data-url", "data-land-url", "data-log-url")
_RELATED_SELECTORS = (
    "#rs a, .rs a, [class*='related'] a, [class*='recomm'] a, "
    ".rw-list a, .c-recomm-wrap a, .opr-recommends-merge-content a"
)


def _extract_related(soup: BeautifulSoup) -> list[str]:
    """提取「相关搜索/大家还在搜」，兼容桌面、移动和动态推荐结构。

    旧版：#rs / .rs 容器内的锚点。新版：相关搜索放在一个含「相关搜索」文字的
    块里，且常由 JS 渲染——静态页拿不到时返回空，交给浏览器路径补齐。
    """
    out: list[str] = []
    for anchor in soup.select(_RELATED_SELECTORS):
        text = anchor.get_text(" ", strip=True)
        if text and 1 < len(text) <= 40 and text not in out:
            out.append(text)

    markers = soup.find_all(
        string=lambda text: text and any(
            label in text for label in ("相关搜索", "大家还在搜", "其他人还搜", "你可能还想搜")
        )
    )
    for marker in markers:
        block = marker.parent
        # 从标题向上寻找最小的、包含多个候选链接的推荐块。
        for _ in range(4):
            if block is None:
                break
            anchors = block.select("a") if hasattr(block, "select") else []
            if len(anchors) >= 2:
                for anchor in anchors:
                    text = anchor.get_text(" ", strip=True)
                    if (
                        text
                        and 1 < len(text) <= 40
                        and text not in out
                        and not any(c in text for c in "，。、！？：")
                    ):
                        out.append(text)
                break
            block = block.parent
    return out[:10]


def _heading_landing_url(heading) -> str:
    """优先取真实落地 URL（data-landurl / mu），避免百度中转链接。

    拿不到真实地址时退回 href（可能是 baidu.php / /link 中转），交给 resolver。
    """
    for attr in _ANCHOR_URL_ATTRS:
        value = (heading.get(attr) or "").strip()
        if value and value.lower() != "null":
            return value
    node = heading.parent
    for _ in range(5):
        if node is None:
            break
        for attr in _CONTAINER_URL_ATTRS:
            value = (node.get(attr) or "").strip() if node.name else ""
            if value and value.lower() != "null":
                return value
        node = node.parent
    return (heading.get("href") or "").strip()


def _heading_snippet(heading) -> str:
    """从标题锚点向上查找摘要节点；找不到返回空串。"""
    node = heading
    for _ in range(5):
        node = node.parent
        if node is None:
            return ""
        found = node.select_one(".c-abstract, [class*='abstract'], [class*='content-right'], .c-span-last")
        if found:
            return found.get_text(" ", strip=True)
    return ""


def parse_serp_html(
    html: str, *, base_url: str = "https://www.baidu.com/s", limit: int = 10
) -> tuple[list[str], list[SearchResult]]:
    """解析百度结果页：相关搜索 + 自然结果的标题与落地 URL。

    直接遍历结果标题锚点（h3 a 等），不依赖外层容器类名——百度新旧模板的容器
    结构差异很大，但结果标题始终是 h3 内的 a。拿不到的字段返回空，不猜测。
    """
    soup = BeautifulSoup(html, "lxml")
    related = _extract_related(soup)

    results: list[SearchResult] = []
    seen: set[str] = set()
    for heading in soup.select("h3 a, h2 a, a.c-title, a.cos-link, a.cosc-title-a"):
        raw_url = _heading_landing_url(heading)
        # 个别广告/聚合结果没有落地地址，不能作为后续竞品页面输入。
        if not raw_url or raw_url.lower() == "null":
            continue
        url = urljoin(base_url, raw_url)
        dedupe = url.split("#", 1)[0]
        if dedupe in seen:
            continue
        title = heading.get_text(" ", strip=True)
        if not title:
            continue
        seen.add(dedupe)
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        snippet = _heading_snippet(heading)
        results.append(SearchResult(len(results) + 1, title, url, domain, snippet))
        if len(results) >= limit:
            break
    return related[:10], results
