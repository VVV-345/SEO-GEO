"""技术 SEO Agent 使用的同域网站发现、抓取和页面事实提取工具。"""
from __future__ import annotations

import ipaddress
import gzip
import json
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections import deque
from time import perf_counter
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from agents.technical_seo_agent.models import AuditPage, LighthouseResult, SiteAuditSnapshot
from tools.lighthouse import run_lighthouse
from tools.progress import ProgressReporter
from tools.webpage import DEFAULT_HEADERS, decode_html_bytes


SKIP_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip",
    ".rar", ".7z", ".mp3", ".mp4", ".avi", ".mov", ".css", ".js", ".xml",
}


def normalize_root_url(domain: str) -> str:
    """把用户输入的域名规范为不带路径的 HTTP(S) 根地址。"""
    value = domain.strip()
    if not value:
        raise ValueError("技术审计必须提供客户网站域名。")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"无效的网站域名：{domain}")
    _validate_public_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _validate_public_host(hostname: str, port: int) -> None:
    """阻止审计输入访问本机、内网、链路本地或保留 IP。"""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port)}
    except socket.gaierror as error:
        raise ValueError(f"无法解析域名：{hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"为安全起见不审计内网或本机地址：{hostname}")


def canonicalize_url(url: str) -> str:
    """移除片段和默认端口，用稳定形式参与去重与内链图计算。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _same_site(url: str, root_url: str) -> bool:
    """判断 URL 是否属于审计根域名，允许 www 与裸域名互相跳转。"""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    root_host = (urlparse(root_url).hostname or "").lower().removeprefix("www.")
    return bool(host) and host == root_host


def _crawlable_html_candidate(url: str) -> bool:
    """过滤明显的静态资源、非 HTTP 链接和会话/登录动作地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    if any(path.endswith(suffix) for suffix in SKIP_SUFFIXES):
        return False
    if any(part in path for part in ("/logout", "/signout", "/wp-admin")):
        return False
    return True


def _schema_data(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    """解析 JSON-LD 类型并记录语法错误，不判断富媒体结果资格。"""
    types: list[str] = []
    errors: list[str] = []
    for index, script in enumerate(soup.find_all("script", attrs={"type": "application/ld+json"}), 1):
        try:
            data = json.loads(script.string or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            errors.append(f"JSON-LD #{index}: {error}")
            continue
        queue = data if isinstance(data, list) else [data]
        for item in queue:
            if not isinstance(item, dict):
                continue
            nested = item.get("@graph", [])
            candidates = [item, *(nested if isinstance(nested, list) else [])]
            for candidate in candidates:
                value = candidate.get("@type") if isinstance(candidate, dict) else None
                for schema_type in value if isinstance(value, list) else [value]:
                    text = str(schema_type or "").strip()
                    if text and text not in types:
                        types.append(text)
    return types, errors


def _parse_sitemap(content: bytes, sitemap_url: str) -> tuple[list[str], list[str]]:
    """解析 Sitemap URL 集或索引，并分别返回页面 URL 与子 Sitemap。"""
    if sitemap_url.lower().endswith(".gz") or content.startswith(b"\x1f\x8b"):
        try:
            content = gzip.decompress(content)
        except OSError as error:
            raise ValueError(f"Sitemap gzip 无法解压：{error}") from error
        if len(content) > 20_000_000:
            raise ValueError("Sitemap 解压后超过 20MB，第一版审计拒绝加载")
    if len(content) > 20_000_000:
        raise ValueError("Sitemap 超过 20MB，第一版审计拒绝加载")
    root = ET.fromstring(content)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    locations = [
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() == "loc" and (node.text or "").strip()
    ]
    if tag == "sitemapindex":
        return [], [urljoin(sitemap_url, value) for value in locations]
    return [urljoin(sitemap_url, value) for value in locations], []


class SiteAuditCrawler:
    """受页面数、同域和请求间隔约束的公共网站审计爬虫。"""

    def __init__(self, *, timeout: float = 15, delay: float = 0.2) -> None:
        """初始化复用会话与低频抓取参数。"""
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def close(self) -> None:
        """关闭底层 HTTP 会话。"""
        self.session.close()

    def __enter__(self) -> "SiteAuditCrawler":
        """进入上下文并返回爬虫实例。"""
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        """离开上下文时释放网络资源。"""
        self.close()

    def _get(self, url: str) -> tuple[requests.Response, list[str], int]:
        """手动跟随至多五次公网重定向，并返回耗时毫秒数。"""
        chain: list[str] = []
        started = perf_counter()
        response = None
        for _ in range(6):
            parsed = urlparse(url)
            if not parsed.hostname:
                raise ValueError(f"无效 URL：{url}")
            _validate_public_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                target = urljoin(url, response.headers.get("location", ""))
                if not target:
                    break
                chain.append(target)
                url = target
                continue
            break
        if response is None:
            raise RuntimeError("页面没有返回响应")
        return response, chain, round((perf_counter() - started) * 1000)

    def _robots(self, root_url: str) -> tuple[str, int | None, str, str, RobotFileParser]:
        """读取 robots.txt，并返回可供 URL 检查复用的解析器。"""
        url = urljoin(root_url, "/robots.txt")
        parser = RobotFileParser()
        parser.set_url(url)
        try:
            response, _, _ = self._get(url)
            text = decode_html_bytes(
                response.content,
                content_type=response.headers.get("content-type", ""),
                apparent_encoding=response.apparent_encoding or "",
            )
            if response.status_code < 400:
                parser.parse(text.splitlines())
            else:
                parser.parse([])
            return url, response.status_code, text, "" if response.status_code < 400 else f"HTTP {response.status_code}", parser
        except Exception as error:
            parser.parse([])
            return url, None, "", str(error), parser

    def _sitemaps(
        self, root_url: str, robots_text: str
    ) -> tuple[list[str], list[str], dict[str, int | None], list[str]]:
        """发现并递归读取有限数量的 Sitemap，返回其中的页面 URL。"""
        declared = re.findall(r"(?im)^\s*Sitemap\s*:\s*(\S+)", robots_text)
        queue = deque(declared or [urljoin(root_url, "/sitemap.xml")])
        visited: list[str] = []
        page_urls: list[str] = []
        statuses: dict[str, int | None] = {}
        errors: list[str] = []
        while queue and len(visited) < 20:
            sitemap_url = canonicalize_url(queue.popleft())
            if sitemap_url in visited or not _same_site(sitemap_url, root_url):
                continue
            visited.append(sitemap_url)
            try:
                response, _, _ = self._get(sitemap_url)
                statuses[sitemap_url] = response.status_code
                if response.status_code >= 400:
                    errors.append(f"{sitemap_url}: HTTP {response.status_code}")
                    continue
                urls, children = _parse_sitemap(response.content, sitemap_url)
                for child in children[:20]:
                    if child not in visited:
                        queue.append(child)
                for url in urls:
                    normalized = canonicalize_url(url)
                    if _same_site(normalized, root_url) and _crawlable_html_candidate(normalized) and normalized not in page_urls:
                        page_urls.append(normalized)
            except Exception as error:
                statuses[sitemap_url] = None
                errors.append(f"{sitemap_url}: {error}")
        return visited, page_urls, statuses, errors

    def _page(
        self, url: str, *, root_url: str, sitemap_set: set[str], robots: RobotFileParser
    ) -> AuditPage:
        """请求一个页面并提取技术字段、内外链和 Schema 事实。"""
        allowed = robots.can_fetch("*", url)
        if not allowed:
            return AuditPage(url=url, in_sitemap=url in sitemap_set, robots_allowed=False, error="robots.txt 禁止抓取")
        try:
            response, chain, elapsed = self._get(url)
        except Exception as error:
            return AuditPage(url=url, in_sitemap=url in sitemap_set, robots_allowed=True, error=str(error))
        content_type = response.headers.get("content-type", "")
        base = canonicalize_url(response.url)
        if "html" not in content_type.lower() and "xhtml" not in content_type.lower():
            return AuditPage(
                url=url, final_url=base, status_code=response.status_code, content_type=content_type,
                redirect_chain=chain, response_time_ms=elapsed, html_size=len(response.content),
                in_sitemap=url in sitemap_set, robots_allowed=True,
            )
        html = decode_html_bytes(
            response.content,
            content_type=content_type,
            apparent_encoding=response.apparent_encoding or "",
        )
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        robots_meta = soup.find("meta", attrs={"name": re.compile(r"^(robots|baiduspider|googlebot)$", re.I)})
        robots_values = [
            str(robots_meta.get("content", "")).strip().lower() if robots_meta else "",
            response.headers.get("x-robots-tag", "").strip().lower(),
        ]
        canonical = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
        schema_types, schema_errors = _schema_data(soup)
        internal: list[str] = []
        external: list[str] = []
        for anchor in soup.select("a[href]"):
            target = canonicalize_url(urljoin(base, str(anchor.get("href", "")).strip()))
            if not _crawlable_html_candidate(target):
                continue
            collection = internal if _same_site(target, root_url) else external
            if target not in collection:
                collection.append(target)
        images = soup.select("img")
        text = soup.get_text(" ", strip=True)
        return AuditPage(
            url=url,
            final_url=base,
            status_code=response.status_code,
            content_type=content_type,
            redirect_chain=chain,
            title=title,
            meta_description=str(meta.get("content", "")).strip() if meta else "",
            h1=[node.get_text(" ", strip=True) for node in soup.select("h1") if node.get_text(" ", strip=True)],
            headings=[node.get_text(" ", strip=True) for node in soup.select("h1, h2, h3") if node.get_text(" ", strip=True)][:80],
            canonical=canonicalize_url(urljoin(base, str(canonical.get("href", "")))) if canonical and canonical.get("href") else "",
            robots_meta=", ".join(value for value in robots_values if value),
            schema_types=schema_types,
            schema_errors=schema_errors,
            internal_links=internal,
            external_links=external,
            image_count=len(images),
            missing_alt_count=sum(not str(image.get("alt", "")).strip() for image in images),
            word_count=len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text)),
            html_size=len(response.content),
            response_time_ms=elapsed,
            in_sitemap=url in sitemap_set,
            robots_allowed=True,
        )

    def crawl(
        self,
        domain: str,
        *,
        core_urls: list[str] | None = None,
        excluded_paths: list[str] | None = None,
        max_pages: int = 50,
        run_lighthouse_checks: bool = True,
        lighthouse_limit: int = 3,
        progress: ProgressReporter | None = None,
    ) -> SiteAuditSnapshot:
        """发现并抓取站内页面，最后对少量代表页执行可选 Lighthouse。"""
        progress = progress or ProgressReporter()
        root_url = normalize_root_url(domain)
        exclusions = [value.strip() for value in (excluded_paths or []) if value.strip()]
        normalized_core = [
            canonicalize_url(urljoin(root_url, value.strip()))
            for value in (core_urls or []) if value.strip()
        ]
        progress.started("technical.discovery", "发现网站", "读取 robots.txt 与 Sitemap")
        robots_url, robots_status, robots_text, robots_error, robots = self._robots(root_url)
        sitemap_urls, sitemap_pages, sitemap_statuses, sitemap_errors = self._sitemaps(root_url, robots_text)
        progress.completed(
            "technical.discovery", "发现网站", f"Sitemap 发现 {len(sitemap_pages)} 个页面 URL"
        )

        sitemap_set = set(sitemap_pages)
        queue = deque([canonicalize_url(root_url), *normalized_core, *sitemap_pages])
        seen: set[str] = set()
        pages: list[AuditPage] = []
        crawl_errors: list[str] = []
        limit = max(1, min(int(max_pages), 500))
        progress.started("technical.crawl", "抓取网站页面", "开始受控同域抓取", total=limit)
        while queue and len(pages) < limit:
            url = canonicalize_url(queue.popleft())
            if url in seen or not _same_site(url, root_url) or not _crawlable_html_candidate(url):
                continue
            if any(urlparse(url).path.startswith(path) for path in exclusions):
                continue
            seen.add(url)
            progress.step(
                "technical.crawl", "抓取网站页面", f"正在检查：{url}", current=len(pages), total=limit
            )
            page = self._page(url, root_url=root_url, sitemap_set=sitemap_set, robots=robots)
            pages.append(page)
            if page.error:
                crawl_errors.append(f"{url}: {page.error}")
            for target in page.internal_links:
                if target not in seen:
                    queue.append(target)
            progress.step(
                "technical.crawl", "抓取网站页面", f"已处理：{url}", current=len(pages), total=limit
            )
            if self.delay:
                time.sleep(self.delay)
        progress.completed(
            "technical.crawl", "抓取网站页面", f"本次处理 {len(pages)} 个页面", total=len(pages)
        )

        inlinks = {page.url: 0 for page in pages}
        known = set(inlinks)
        for page in pages:
            for target in page.internal_links:
                if target in known:
                    inlinks[target] += 1

        lighthouse: list[LighthouseResult] = []
        if run_lighthouse_checks:
            candidates = list(dict.fromkeys([
                *normalized_core,
                canonicalize_url(root_url),
                *(page.url for page in pages if page.status_code == 200),
            ]))[:max(1, min(lighthouse_limit, 10))]
            progress.started(
                "technical.lighthouse", "运行 Lighthouse", "检测少量代表页面的实验室性能", total=len(candidates)
            )
            for index, url in enumerate(candidates, 1):
                result = run_lighthouse(url)
                lighthouse.append(result)
                progress.step(
                    "technical.lighthouse", "运行 Lighthouse",
                    f"{'完成' if result.available else '跳过'}：{url}", current=index, total=len(candidates)
                )
            progress.completed(
                "technical.lighthouse", "运行 Lighthouse", "代表页面性能检测结束", total=len(candidates)
            )

        notes = [
            f"最大抓取页数设置为 {limit}；发现但未抓取的 URL 不会被当成已检查页面。",
            "孤立页判断仅基于本次抓取形成的内链图，属于人工复核项。",
        ]
        if queue:
            notes.append(f"达到页面上限时仍有 {len(queue)} 个待发现 URL，审计属于抽样覆盖。")
        if run_lighthouse_checks and lighthouse and not any(item.available for item in lighthouse):
            notes.append(f"Lighthouse 未执行成功：{lighthouse[0].error}")
        return SiteAuditSnapshot(
            root_url=root_url,
            robots_url=robots_url,
            robots_status=robots_status,
            robots_text=robots_text,
            robots_error=robots_error,
            sitemap_urls=sitemap_urls,
            sitemap_statuses=sitemap_statuses,
            sitemap_errors=sitemap_errors,
            discovered_urls=list(seen | set(queue) | sitemap_set),
            pages=pages,
            inlink_counts=inlinks,
            crawl_errors=crawl_errors,
            lighthouse=lighthouse,
            coverage_notes=notes,
        )
