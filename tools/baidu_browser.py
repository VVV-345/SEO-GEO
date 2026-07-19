"""仅在 requests 无法取得百度自然结果时启用的 Playwright 回退。

该工具只读取百度搜索结果页的标题和 URL，不打开或解析竞品页面正文。
"""
from __future__ import annotations

from urllib.parse import quote_plus, urlparse

from .baidu_serp import SearchResult, is_baidu_verification_page, parse_serp_html


class BaiduBrowserFallback:
    def __init__(self, *, timeout_ms: int = 20_000, channel: str = "msedge") -> None:
        self.timeout_ms = timeout_ms
        self.channel = channel
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_started(self) -> None:
        """延迟启动 Edge；静态解析成功时不会创建浏览器进程。"""
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("浏览器回退需要安装 playwright：pip install -r requirements.txt") from error
        self._playwright = sync_playwright().start()
        try:
            # 使用系统已安装的 Edge，不要求另行下载 Playwright Chromium。
            self._browser = self._playwright.chromium.launch(channel=self.channel, headless=True)
            self._context = self._browser.new_context(
                locale="zh-CN",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise

    def search_results(self, keyword: str, *, limit: int = 10) -> tuple[list[str], list[SearchResult]]:
        self._ensure_started()
        assert self._context is not None
        page = self._context.new_page()
        try:
            page.goto(
                f"https://www.baidu.com/s?wd={quote_plus(keyword)}&rn={limit}&ie=utf-8",
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            page.wait_for_timeout(1_200)
            # 部分“相关搜索/大家还在搜”只有滚动到页面底部后才渲染。
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            html = page.content()
            if is_baidu_verification_page(html):
                raise RuntimeError("百度浏览器页面要求安全验证")
            related, results = parse_serp_html(html, base_url=page.url, limit=limit)
            # 页面脚本可能把最终地址放在 DOM property 中；补读取当前 href。
            if not results:
                results = self._extract_links_from_dom(page, limit)
            return related, results
        finally:
            page.close()

    @staticmethod
    def _extract_links_from_dom(page, limit: int) -> list[SearchResult]:
        items: list[SearchResult] = []
        locators = page.locator("#content_left h3 a, #results h3 a, .c-result h3 a")
        for index in range(min(locators.count(), limit)):
            link = locators.nth(index)
            url = (link.get_attribute("href") or "").strip()
            title = link.inner_text().strip()
            if not url.startswith(("http://", "https://")):
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            items.append(SearchResult(len(items) + 1, title, url, domain))
        return items

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._context = self._browser = self._playwright = None
