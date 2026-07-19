"""一次完整 SEO/GEO 分析的统一输出目录与运行清单。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def safe_path_name(value: str, *, fallback: str = "project", max_length: int = 80) -> str:
    """清理 Windows 不允许的路径字符，同时保留可读的中文项目名。"""
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", value).strip(" ._")
    return value[:max_length] or fallback


@dataclass(frozen=True)
class RunContext:
    """所有 Agent 共享的运行目录；同一次分析始终复用本对象。"""

    project_name: str
    run_id: str
    root_dir: Path

    @classmethod
    def create(
        cls,
        *,
        output_root: str | Path = "output",
        project_name: str,
        now: datetime | None = None,
    ) -> "RunContext":
        """创建北京时间戳运行目录，并为同秒重复运行追加序号。"""
        created = (now or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
        project = safe_path_name(project_name)
        base_run_id = created.strftime("%Y%m%d_%H%M%S")
        project_dir = Path(output_root) / project
        run_id = base_run_id
        root = project_dir / run_id
        suffix = 1
        # 同一秒内重复点击也不能覆盖上一次分析。
        while root.exists():
            run_id = f"{base_run_id}_{suffix:02d}"
            root = project_dir / run_id
            suffix += 1
        context = cls(project, run_id, root)
        for name in ("input", "keyword", "competitor"):
            context.agent_dir(name)
        context.update_run(
            status="running",
            current_stage="input",
            completed_agents=[],
            failed_agents=[],
            created_at=created.isoformat(),
        )
        return context

    def agent_dir(self, name: str) -> Path:
        """返回指定 Agent 的目录；目录不存在时安全创建。"""
        path = self.root_dir / safe_path_name(name, fallback="agent")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def run_file(self) -> Path:
        """返回记录整次工作流状态的 run.json 路径。"""
        return self.root_dir / "run.json"

    def update_run(self, **changes: Any) -> None:
        """合并更新运行状态，保留其他 Agent 已写入的字段。"""
        payload: dict[str, Any] = {
            "project_name": self.project_name,
            "run_id": self.run_id,
            "root_dir": str(self.root_dir),
        }
        if self.run_file.exists():
            try:
                payload.update(json.loads(self.run_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        payload.update(changes)
        payload["updated_at"] = datetime.now(CHINA_TZ).isoformat()
        self.run_file.parent.mkdir(parents=True, exist_ok=True)
        self.run_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_source_manifest(paths: list[str]) -> list[dict[str, Any]]:
    """只记录资料元信息，不复制客户原文件。"""
    manifest = []
    for value in paths:
        path = Path(value)
        item: dict[str, Any] = {"path": str(path), "name": path.name, "exists": path.is_file()}
        if path.is_file():
            item["size_bytes"] = path.stat().st_size
        manifest.append(item)
    return manifest
