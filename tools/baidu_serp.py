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
from urllib.parse import urljoin, urlparse

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
class BaiduSERP:
    keyword: str
    suggestions: list[str] = field(default_factory=list)
    related_searches: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    complete: bool = False
    error: str = ""


class SERPFallback(Protocol):
    """浏览器等备用采集器的最小接口，便于注入测试或替换实现。"""

    def search_results(self, keyword: str, *, limit: int = 10) -> tuple[list[str], list[SearchResult]]: ...

    def close(self) -> None: ...


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
            results = self._finalize_results(results)
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
            results = self._finalize_results(results)
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

    def _finalize_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """只解析前 N 条的真实落地 URL，其余丢弃。

        超出 resolve_top_n 的结果只携带百度中转 URL，保留会让竞争度评分把
        domain 误判成 baidu.com，因此截断而不是保留未解析项。
        """
        cap = min(self.resolve_top_n, len(results))
        return [self._resolve_result(item) for item in results[:cap]]

    def _search_mobile(self, keyword: str, *, limit: int) -> tuple[list[str], list[SearchResult], str]:
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
        if result.domain not in {"baidu.com", "www.baidu.com", "m.baidu.com"} or "/link" not in result.url:
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
