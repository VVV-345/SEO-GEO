"""下载网页并提取适合交给 Agent 的清洗内容。"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup


# 通用浏览器请求头，网页与百度采集共用。
# Referer / Sec-Fetch-Site 等上下文相关头由各采集器按目标站点补充。
# User-Agent 与 Sec-Ch-Ua 的主版本需保持一致，并随当前 Chrome 主版本更新。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="138", "Not:A-Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


@dataclass(frozen=True)
class WebPageContent:
    """网页清洗后的统一结构，供所有 Agent 复用。"""

    requested_url: str
    final_url: str
    status_code: int
    title: str
    description: str
    headings: list[str] = field(default_factory=list)
    heading_structure: list[dict[str, str | int]] = field(default_factory=list)
    faq_questions: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    case_mentions: list[str] = field(default_factory=list)
    data_points: list[str] = field(default_factory=list)
    text: str = ""


def decode_html_bytes(
    content: bytes,
    *,
    content_type: str = "",
    apparent_encoding: str = "",
) -> str:
    """根据 BOM、HTML meta、HTTP charset 和中文常见编码解码网页字节。

    ``requests.Response.text`` 在响应未声明 charset 时可能默认使用 ISO-8859-1，
    造成 UTF-8 中文变成 ``å¤§æ¨¡...``。这里直接处理原始字节，并把
    ISO-8859-1 这类无信息的默认值放到最后，避免乱码进入后续 Agent。
    """
    if not content:
        return ""
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig", errors="replace")

    head = content[:16_384]
    meta_match = re.search(
        br"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
        head,
        re.I,
    )
    header_match = re.search(r"charset\s*=\s*([a-zA-Z0-9._-]+)", content_type, re.I)
    candidates = [
        header_match.group(1) if header_match else "",
        meta_match.group(1).decode("ascii", errors="ignore") if meta_match else "",
        "utf-8",
        apparent_encoding,
        "gb18030",
    ]
    ignored_defaults = {"iso-8859-1", "latin-1", "latin1", "ascii"}
    tried: set[str] = set()
    fallback = ""
    for raw_encoding in candidates:
        encoding = raw_encoding.strip().lower()
        if not encoding or encoding in tried:
            continue
        tried.add(encoding)
        try:
            decoded = content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        if encoding not in ignored_defaults:
            return decoded
        fallback = fallback or decoded
    return fallback or content.decode("utf-8", errors="replace")


def _validate_public_url(url: str) -> None:
    """仅允许公开 HTTP(S) 地址，防止 URL 输入被用于读取本机或内网服务。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"只支持公开的 http/https URL：{url}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 80)}
    except socket.gaierror as error:
        raise ValueError(f"无法解析域名：{parsed.hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"为安全起见不读取内网或本机地址：{parsed.hostname}")


def _unique_texts(values: list[str], *, limit: int) -> list[str]:
    """按出现顺序清理和去重文本，防止导航或 Schema 重复污染页面证据。"""
    output: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", value).strip()
        if text and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _extract_faq_questions(soup: BeautifulSoup) -> list[str]:
    """从 FAQ JSON-LD、FAQ 区域和疑问式标题中提取用户问题。"""
    questions: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            import json

            data = json.loads(script.string or "{}")
        except (TypeError, ValueError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph", [])
            candidates = [node, *(graph if isinstance(graph, list) else [])]
            for candidate in candidates:
                if not isinstance(candidate, dict) or candidate.get("@type") != "FAQPage":
                    continue
                for entity in candidate.get("mainEntity", []):
                    if isinstance(entity, dict):
                        questions.append(str(entity.get("name", "")))
    for heading in soup.select("h1, h2, h3, summary, [class*='faq'] dt, [class*='faq'] h4"):
        text = heading.get_text(" ", strip=True)
        if any(mark in text for mark in ("?", "？", "如何", "怎么", "什么", "为什么", "是否", "多久", "多少")):
            questions.append(text)
    return _unique_texts(questions, limit=30)


def _extract_tables(soup: BeautifulSoup) -> list[str]:
    """把 HTML 表格压缩为可读行文本，便于模型识别价格、参数和对比信息。"""
    tables: list[str] = []
    for table in soup.select("table")[:12]:
        rows = []
        for row in table.select("tr")[:30]:
            cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            tables.append("\n".join(rows)[:4_000])
    return tables


def _extract_case_and_data(text: str) -> tuple[list[str], list[str]]:
    """从正文中截取案例和数字证据原句；只做识别，不推断数字含义。"""
    units = r"(?:%|％|元|万元|亿元|人|家|个|项|天|周|月|年|小时|分钟|GB|TB|倍)"
    case_mentions: list[str] = []
    data_points: list[str] = []
    for raw in re.split(r"[\n。！？!?]+", text):
        sentence = re.sub(r"\s+", " ", raw).strip()
        if not sentence or len(sentence) > 300:
            continue
        if any(marker in sentence for marker in ("案例", "客户", "项目实践", "落地实践", "成功部署")):
            case_mentions.append(sentence)
        if re.search(rf"\d+(?:\.\d+)?\s*{units}", sentence, re.I):
            data_points.append(sentence)
    return _unique_texts(case_mentions, limit=20), _unique_texts(data_points, limit=30)


def clean_html_details(html: str, url: str = "") -> dict[str, object]:
    """提取标题、描述、分级标题、FAQ、表格和正文等完整页面证据。"""
    soup = BeautifulSoup(html, "lxml")
    heading_structure = [
        {"level": int(node.name[1]), "text": node.get_text(" ", strip=True)}
        for node in soup.select("h1, h2, h3")
        if node.get_text(" ", strip=True)
    ][:80]
    faq_questions = _extract_faq_questions(soup)
    tables = _extract_tables(soup)
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = str(meta.get("content", "")).strip() if meta else ""
    headings = [str(item["text"]) for item in heading_structure]
    extracted = trafilatura.extract(
        html,
        url=url or None,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    text = extracted or soup.get_text("\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    case_mentions, data_points = _extract_case_and_data(text)
    return {
        "title": title,
        "description": description,
        "headings": headings,
        "heading_structure": heading_structure,
        "faq_questions": faq_questions,
        "tables": tables,
        "case_mentions": case_mentions,
        "data_points": data_points,
        "text": text,
    }


def clean_html(html: str, url: str = "") -> tuple[str, str, list[str], str]:
    """保留旧版四元组接口；新代码需要更多字段时调用 ``clean_html_details``。"""
    details = clean_html_details(html, url)
    return (
        str(details["title"]),
        str(details["description"]),
        list(details["headings"]),
        str(details["text"]),
    )


def fetch_webpage(url: str, *, timeout: float = 15, max_chars: int = 40_000) -> WebPageContent:
    """下载公开 HTML 页面并返回已清洗证据；逐跳校验重定向以阻止内网访问。"""
    _validate_public_url(url)
    requested_url = url
    response = None
    # 手动跟随重定向，确保每一跳都经过公网地址校验。
    for _ in range(6):
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError("网页返回重定向，但没有 Location 地址")
            url = urljoin(url, location)
            _validate_public_url(url)
            continue
        break
    else:
        raise ValueError("网页重定向次数超过 5 次")
    assert response is not None
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower() and "xhtml" not in content_type.lower():
        raise ValueError(f"URL 返回的不是 HTML 页面：{content_type or '未知类型'}")
    html = decode_html_bytes(
        response.content,
        content_type=content_type,
        apparent_encoding=response.apparent_encoding or "",
    )
    details = clean_html_details(html, response.url)
    return WebPageContent(
        requested_url=requested_url,
        final_url=response.url,
        status_code=response.status_code,
        title=str(details["title"]),
        description=str(details["description"]),
        headings=list(details["headings"]),
        heading_structure=list(details["heading_structure"]),
        faq_questions=list(details["faq_questions"]),
        tables=list(details["tables"]),
        case_mentions=list(details["case_mentions"]),
        data_points=list(details["data_points"]),
        text=str(details["text"])[:max_chars],
    )
