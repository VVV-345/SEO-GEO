"""SEO-GEO 统一命令行入口。"""
from __future__ import annotations

import argparse
import sys

# Windows 控制台可能默认使用 GBK；在导入业务模块和输出进度前统一为 UTF-8。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from app import run_keyword_workflow, write_keyword_output
from tools.progress import ProgressEvent, ProgressReporter


def print_progress(event: ProgressEvent) -> None:
    """CLI 的事件订阅者；其他前端可使用同一事件渲染不同界面。"""
    suffix = f" [{event.current}/{event.total}]" if event.current is not None and event.total else ""
    print(f"- {event.label}：{event.message}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEO/GEO 多 Agent 系统")
    subparsers = parser.add_subparsers(dest="agent", required=True)
    keyword = subparsers.add_parser("keyword", help="运行关键词机会 Agent")
    keyword.add_argument("--seed", nargs="+", required=True, help="一个或多个种子词")
    keyword.add_argument("--files", nargs="*", default=[], help="客户资料文件，可多个")
    keyword.add_argument("--requirement", default="", help="本次关键词研究的需求描述")
    keyword.add_argument("--pages", nargs="*", default=[], help="客户已有页面 URL，可多个")
    keyword.add_argument("--num", type=int, default=30, help="候选词数量上限")
    keyword.add_argument("--serp-limit", type=int, default=10, help="每词提取的百度自然结果数")
    keyword.add_argument("--mock", action="store_true", help="完全离线测试，不调用 LLM/百度/客户 URL")
    keyword.add_argument("-o", "--output-dir", default="output")
    args = parser.parse_args(argv)

    progress = ProgressReporter([print_progress])
    result = run_keyword_workflow(
        seeds=args.seed,
        material_files=args.files,
        page_urls=args.pages,
        requirement=args.requirement,
        candidate_limit=args.num,
        serp_limit=args.serp_limit,
        mock=args.mock,
        progress=progress,
    )
    json_path, markdown_path = write_keyword_output(result, args.output_dir)
    print(f"完成：{len(result.opportunities)} 个关键词机会")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
