import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import (
    build_selected_keyword_output,
    create_run_context,
    fetch_keyword_serp,
    generate_keyword_candidates,
    write_candidate_output,
    write_selected_keyword_output,
)
from core.run_context import CHINA_TZ, RunContext, safe_path_name


class TestRunContext(unittest.TestCase):
    def test_creates_project_timestamp_and_agent_directories(self):
        """新运行应创建项目、时间戳和各 Agent 目录。"""
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 7, 19, 15, 30, 45, tzinfo=CHINA_TZ)
            run = RunContext.create(output_root=directory, project_name="企业知识库", now=now)
            self.assertEqual(run.run_id, "20260719_153045")
            self.assertTrue((run.root_dir / "input").is_dir())
            self.assertTrue((run.root_dir / "keyword").is_dir())
            self.assertTrue((run.root_dir / "competitor").is_dir())
            self.assertTrue((run.root_dir / "technical_seo").is_dir())
            payload = json.loads(run.run_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["project_name"], "企业知识库")

    def test_same_second_does_not_overwrite(self):
        """同一秒内重复运行应追加序号而不覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 7, 19, 15, 30, 45, tzinfo=CHINA_TZ)
            first = RunContext.create(output_root=directory, project_name="项目", now=now)
            second = RunContext.create(output_root=directory, project_name="项目", now=now)
            self.assertNotEqual(first.root_dir, second.root_dir)
            self.assertEqual(second.run_id, "20260719_153045_01")

    def test_safe_project_name(self):
        """项目名中的 Windows 非法字符应被安全替换。"""
        self.assertEqual(safe_path_name('企业:知识库/测试?'), "企业_知识库_测试")

    def test_two_stage_outputs_share_one_run_directory(self):
        """候选和已选 SERP 结果必须写入同一个运行目录。"""
        with tempfile.TemporaryDirectory() as directory:
            candidates = generate_keyword_candidates(seeds=["企业知识库"], mock=True)
            run = create_run_context(candidates.seeds, output_root=directory)
            candidate_json, candidate_report = write_candidate_output(candidates, run)
            selected = [candidates.candidates[0].keyword]
            serp = fetch_keyword_serp(selected, mock=True)
            final = build_selected_keyword_output(candidates, selected, serp, mock=True)
            serp_path, opportunities_path, report_path = write_selected_keyword_output(final, serp, run)
            for path in (candidate_json, candidate_report, serp_path, opportunities_path, report_path):
                self.assertTrue(path.is_file())
                self.assertTrue(path.is_relative_to(run.root_dir))
            self.assertEqual(candidate_json.parent, report_path.parent)
            self.assertTrue((run.root_dir / "input" / "project.json").is_file())
            self.assertTrue((run.root_dir / "input" / "source_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
