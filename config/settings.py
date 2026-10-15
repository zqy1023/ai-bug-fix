"""项目级默认配置集中定义。

环境变量仍由 ``app.infrastructure.config`` 负责覆盖；本模块只保存不应散落在业务代码中的
默认值和可复用限制，便于部署时统一审阅和调整。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """应用默认设置。"""

    default_output_dir: Path = ROOT / "output"
    runtime_dir_name: str = ".ai-repair"
    artifact_dir_name: str = ".ai-repair"
    default_service_name: str = "gather.py.track"
    default_log_limit: int = 0
    default_log_days: int = 1
    default_index_days: int = 2
    default_index_tz_offset: int = 0
    default_log_tz_offset: int = 8
    default_max_attempts: int = 3
    graph_retry_attempts: int = 2
    graph_recursion_limit: int = 40
    default_git_remote: str = "origin"
    repair_branch_prefix: str = "ai-repair"
    default_git_push: bool = False
    default_autorun: bool = False
    default_review_mode: str = "agent"
    default_ignore_errors: tuple[str, ...] = ("redis.exceptions.ConnectionError",)
    # Kibana 看板口径：前缀加精确 service_name，避免 gather.py 其他服务混入。
    default_service_prefix: str = "gather.py"
    excluded_biztypes: tuple[str, ...] = (
        "framework.controllers.cache_controller",
        "framework.task_manager.result_sender",
    )
    repair_branch_pattern: re.Pattern[str] = re.compile(
        r"^ai-repair/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?$"
    )
    max_commit_comment_chars: int = 800
    max_diff_summary_chars: int = 8000
    evidence_max_scan_files: int = 300
    evidence_max_read_bytes: int = 524288
    evidence_max_symbols: int = 32
    evidence_query_value_chars: int = 600
    evidence_extracted_value_chars: int = 500
    max_sessions: int = 8
    es_timeout_seconds: int = 30
    agent_progress: bool = True
    agent_archive: bool = True
    agent_archive_path: Path = ROOT / "runtime" / "agent-hooks.jsonl"


settings = Settings()

__all__ = ["ROOT", "Settings", "settings"]
