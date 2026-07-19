"""可选的本地 Lighthouse CLI 适配器。

该工具只运行用户明确提交给技术审计的公开页面，并把实验室结果与真实用户数据
严格区分。没有安装 Lighthouse 或浏览器时返回可读错误，不伪造分数。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agents.technical_seo_agent.models import LighthouseResult


def find_chrome() -> str:
    """查找 Lighthouse 可使用的 Chrome/Edge 可执行文件。"""
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def lighthouse_availability() -> tuple[str, str]:
    """返回 Lighthouse CLI 和浏览器路径；缺失项用空字符串表示。"""
    return shutil.which("lighthouse") or "", find_chrome()


def _score(report: dict, category: str) -> int | None:
    """将 Lighthouse 的 0-1 分类分数转换为 0-100。"""
    value = report.get("categories", {}).get(category, {}).get("score")
    return round(float(value) * 100) if isinstance(value, (int, float)) else None


def _numeric_audit(report: dict, audit_id: str) -> float | None:
    """安全读取 Lighthouse audit 的 numericValue。"""
    value = report.get("audits", {}).get(audit_id, {}).get("numericValue")
    return round(float(value), 2) if isinstance(value, (int, float)) else None


def run_lighthouse(url: str, *, timeout: int = 120) -> LighthouseResult:
    """运行一次本地 Lighthouse 并提取核心实验室指标。"""
    executable, chrome = lighthouse_availability()
    if not executable:
        return LighthouseResult(url=url, available=False, error="未安装 Lighthouse CLI")
    if not chrome:
        return LighthouseResult(url=url, available=False, error="未找到 Chrome 或 Edge")
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "report.json"
        command = [
            executable,
            url,
            "--output=json",
            f"--output-path={output}",
            "--quiet",
            "--chrome-flags=--headless --no-sandbox --disable-gpu",
        ]
        environment = os.environ.copy()
        environment["CHROME_PATH"] = chrome
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return LighthouseResult(url=url, available=False, error=f"Lighthouse 执行失败：{error}")
        if completed.returncode != 0 or not output.is_file():
            message = (completed.stderr or completed.stdout or "未知错误").strip()[-500:]
            return LighthouseResult(url=url, available=False, error=f"Lighthouse 未生成报告：{message}")
        try:
            report = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return LighthouseResult(url=url, available=False, error=f"Lighthouse 报告无法解析：{error}")
    return LighthouseResult(
        url=url,
        available=True,
        performance=_score(report, "performance"),
        seo=_score(report, "seo"),
        accessibility=_score(report, "accessibility"),
        best_practices=_score(report, "best-practices"),
        lcp_ms=_numeric_audit(report, "largest-contentful-paint"),
        cls=_numeric_audit(report, "cumulative-layout-shift"),
        tbt_ms=_numeric_audit(report, "total-blocking-time"),
    )
