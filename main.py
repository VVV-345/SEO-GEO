"""SEO-GEO 统一命令行入口。"""
from __future__ import annotations

import argparse
import sys

# Windows 控制台可能默认使用 GBK；在导入业务模块和输出进度前统一为 UTF-8。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from app import (
    create_run_context,
    run_keyword_workflow,
    run_technical_seo_audit,
    write_keyword_output,
    write_technical_seo_output,
)
from tools.progress import ProgressEvent, ProgressReporter


def print_progress(event: ProgressEvent) -> None:
    """CLI 的事件订阅者；其他前端可使用同一事件渲染不同界面。"""
    suffix = f" [{event.current}/{event.total}]" if event.current is not None and event.total else ""
    print(f"- {event.label}：{event.message}{suffix}")


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数，运行指定 Agent，并返回进程退出码。"""
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
    technical = subparsers.add_parser("technical-seo", help="运行技术 SEO 审计 Agent")
    technical.add_argument("--domain", required=True, help="客户网站域名")
    technical.add_argument("--goal", default="", help="本次审计目标")
    technical.add_argument("--files", nargs="*", default=[], help="客户业务资料文件")
    technical.add_argument("--core-urls", nargs="*", default=[], help="需要重点审计的核心页面")
    technical.add_argument("--exclude-paths", nargs="*", default=[], help="不抓取的路径前缀")
    technical.add_argument("--max-pages", type=int, default=50, help="最大抓取页面数")
    technical.add_argument("--no-lighthouse", action="store_true", help="跳过 Lighthouse 代表页检测")
    technical.add_argument("--mock", action="store_true", help="离线验证，不访问网站或真实模型")
    technical.add_argument("-o", "--output-dir", default="output")
    args = parser.parse_args(argv)

    progress = ProgressReporter([print_progress])
    if args.agent == "technical-seo":
        project = args.domain.replace("https://", "").replace("http://", "").split("/", 1)[0]
        run = create_run_context([], output_root=args.output_dir, project_name=project)
        request, snapshot, output = run_technical_seo_audit(
            domain=args.domain,
            audit_goal=args.goal,
            material_files=args.files,
            core_urls=args.core_urls,
            excluded_paths=args.exclude_paths,
            max_pages=args.max_pages,
            run_lighthouse=not args.no_lighthouse,
            mock=args.mock,
            progress=progress,
        )
        json_path, markdown_path = write_technical_seo_output(request, snapshot, output, run)
        print(f"完成：{len(output.findings)} 组技术 SEO 问题")
        print(f"JSON: {json_path}")
        print(f"Markdown: {markdown_path}")
        print(f"Run: {run.root_dir}")
        return 0

    run = create_run_context(args.seed, output_root=args.output_dir)
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
    json_path, markdown_path = write_keyword_output(result, args.output_dir, run=run)
    print(f"完成：{len(result.opportunities)} 个关键词机会")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    print(f"Run: {run.root_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
