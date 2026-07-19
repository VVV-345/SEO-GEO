"""按用户选择查询百度 SERP URL 的独立工具。

候选生成阶段只调用轻量下拉词接口；自然结果、相关搜索和 URL 只有在用户勾选后
才查询。单词重试可新建本工具，不需要重跑已成功的关键词。
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

import requests

from .baidu_browser import BaiduBrowserFallback
from .baidu_serp import BAIDU_HEADERS, BaiduSERP, BaiduSERPClient


class SerpURLTool:
    """可作为上下文管理器使用的百度 URL 查询工具。"""

    def __init__(self, *, timeout: float = 12, delay: float = 3.0, browser: bool = True) -> None:
        """创建带可选浏览器回退的百度 SERP 客户端。"""
        fallback = BaiduBrowserFallback() if browser else None
        self.client = BaiduSERPClient(timeout=timeout, delay=delay, browser_fallback=fallback)

    def fetch(self, keyword: str, *, limit: int = 10) -> BaiduSERP:
        """查询单个关键词；失败结果携带 error，可稍后仅重试该词。"""
        return self.client.search(keyword, limit=limit)

    def fetch_many(
        self,
        keywords: list[str],
        *,
        limit: int = 10,
        on_item: Callable[[int, int, str, BaiduSERP], None] | None = None,
    ) -> dict[str, BaiduSERP]:
        """只查询传入的关键词，不扩展、不隐式加入其他候选词。"""
        results: dict[str, BaiduSERP] = {}
        total = len(keywords)
        for index, keyword in enumerate(keywords, 1):
            result = self.fetch(keyword, limit=limit)
            results[keyword] = result
            if on_item:
                on_item(index, total, keyword, result)
        return results

    def close(self) -> None:
        """关闭 HTTP 会话与可能启动的浏览器。"""
        self.client.close()

    def __enter__(self) -> "SerpURLTool":
        """进入上下文并返回工具本身。"""
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        """退出上下文时始终释放网络和浏览器资源。"""
        self.close()


def get_baidu_suggestions(keyword: str, *, timeout: float = 10) -> tuple[list[str], str]:
    """仅获取百度下拉词，不请求自然结果页，因此不会产生 URL 查询。"""
    try:
        response = requests.get(
            "https://suggestion.baidu.com/su",
            params={"wd": keyword, "action": "opensearch"},
            headers=BAIDU_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        try:
            data = response.json()
        except requests.JSONDecodeError:
            match = re.search(r"\((.*)\)\s*;?$", response.text.strip(), re.S)
            data = json.loads(match.group(1)) if match else []
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            return [str(item).strip() for item in data[1] if str(item).strip()][:10], ""
        if isinstance(data, dict):
            return [str(item).strip() for item in data.get("s", []) if str(item).strip()][:10], ""
        return [], "百度下拉接口返回了未知结构"
    except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
        return [], str(error)


def collect_suggestions(
    keywords: list[str], *, delay: float = 0.4
) -> dict[str, tuple[list[str], str]]:
    """为候选列表补下拉词；不触发自然结果或浏览器查询。"""
    output: dict[str, tuple[list[str], str]] = {}
    for keyword in keywords:
        output[keyword] = get_baidu_suggestions(keyword)
        if delay:
            time.sleep(delay)
    return output
