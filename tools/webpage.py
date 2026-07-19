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
    requested_url: str
    final_url: str
    status_code: int
    title: str
    description: str
    headings: list[str] = field(default_factory=list)
    text: str = ""


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


def clean_html(html: str, url: str = "") -> tuple[str, str, list[str], str]:
    """返回 SEO 元信息和正文；正文提取失败时退回到清理后的可见文本。"""
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = str(meta.get("content", "")).strip() if meta else ""
    headings = [node.get_text(" ", strip=True) for node in soup.select("h1, h2, h3")]
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
    return title, description, headings[:80], text


def fetch_webpage(url: str, *, timeout: float = 15, max_chars: int = 40_000) -> WebPageContent:
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
    title, description, headings, text = clean_html(response.text, response.url)
    return WebPageContent(requested_url, response.url, response.status_code, title, description, headings, text[:max_chars])
