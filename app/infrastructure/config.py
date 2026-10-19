"""新工作流运行配置。"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from config.settings import settings


@dataclass(frozen=True)
class ClaudeConfig:
    """Claude Agent SDK 网关和模型配置。"""

    # auth_token：模型服务认证令牌。
    auth_token: str
    # base_url：模型服务 API 基础地址。
    base_url: str
    # model：默认模型标识。
    model: str
    # default_haiku_model：快速任务模型标识。
    default_haiku_model: str
    # default_opus_model：复杂任务模型标识。
    default_opus_model: str
    # default_sonnet_model：标准任务模型标识。
    default_sonnet_model: str

    def as_env(self) -> dict[str, str]:
        """返回 SDK 所需的非空环境变量。"""
        values = {
            "ANTHROPIC_AUTH_TOKEN": self.auth_token,
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.default_haiku_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.default_opus_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.default_sonnet_model,
        }
        return {key: value for key, value in values.items() if value}


@dataclass(frozen=True)
class RuntimeConfig:
    """ES 发现、执行记录和输出所需的显式配置。"""

    # log_es：ES 连接地址。
    log_es: str
    # output_dir：输出目录。
    output_dir: Path
    # runtime_dir：运行数据与审计目录。
    runtime_dir: Path
    # ignore_errors：忽略错误清单。
    ignore_errors: list[str]
    # limit：日志条数上限。
    limit: int
    # log_service_name：日志服务精确匹配名称。
    log_service_name: str
    # log_service_prefix：日志服务前缀筛选。
    log_service_prefix: str
    # excluded_biztypes：排除的业务类型。
    excluded_biztypes: list[str]
    # log_days：日志回溯天数。
    log_days: int
    # log_tz_offset：日志时区偏移。
    log_tz_offset: int
    # source_repo：源 Git 仓库路径。
    source_repo: Path | None
    # project_subdir：仓库内项目子目录。
    project_subdir: Path | None
    # verify_commands：验证命令数组。
    verify_commands: list[list[str]]
    # git_remote：Git 远程名。
    git_remote: str
    # git_push：是否自动推送。
    git_push: bool
    # review_mode：审查模式。
    review_mode: str
    # max_attempts：单任务最大修复尝试次数。
    max_attempts: int
    # claude：模型网关配置。
    claude: ClaudeConfig

    @property
    def workspace_root(self) -> Path:
        """共享 AI 工作区、验证和回归工作区的根目录。"""
        return self.runtime_dir / "workspaces"

    def agent_env(self) -> dict[str, str]:
        """返回生产 agent 配置与 provider 共用的环境变量。"""
        return {**os.environ, **self.claude.as_env()}


def load_config() -> RuntimeConfig:
    """从项目根目录 `.env` 加载运行配置。"""
    root = Path(__file__).resolve().parents[2]
    # 环境变量优先于 .env 文件：支持容器化部署和测试隔离。
    load_dotenv(root / ".env", override=False)
    raw_ignore = os.getenv("LOG_PULL_IGNORE_ERRORS", "")
    raw_commands = os.getenv("AI_REPAIR_VERIFY_COMMANDS", "")
    try:
        verify_commands = json.loads(raw_commands) if raw_commands else []
    except json.JSONDecodeError:
        verify_commands = [shlex.split(raw_commands)] if raw_commands else []
    if not isinstance(verify_commands, list) or any(
        not isinstance(command, list) or not all(isinstance(item, str) for item in command)
        for command in verify_commands
    ):
        raise ValueError("AI_REPAIR_VERIFY_COMMANDS 必须是字符串数组的 JSON")
    raw_repo = os.getenv("AI_REPAIR_SOURCE_REPO", "").strip()
    raw_project_subdir = os.getenv("AI_REPAIR_PROJECT_SUBDIR", "").strip()
    raw_runtime_dir = os.getenv("AI_REPAIR_RUNTIME_DIR", "").strip()
    review_mode = os.getenv("AI_REPAIR_REVIEW_MODE", "agent").strip().lower()
    if review_mode not in ("agent", "human"):
        raise ValueError("AI_REPAIR_REVIEW_MODE 只能是 agent 或 human")
    return RuntimeConfig(
        log_es=os.getenv("LOG_ES", ""),
        output_dir=Path(os.getenv("OUTPUT_DIR", str(settings.default_output_dir))),
        runtime_dir=(
            Path(raw_runtime_dir).resolve()
            if raw_runtime_dir
            else root / settings.runtime_dir_name
        ),
        ignore_errors=_text_list(raw_ignore, settings.default_ignore_errors),
        limit=int(os.getenv("LOG_PULL_LIMIT", str(settings.default_log_limit))),
        log_service_name=os.getenv("LOG_PULL_SERVICE_NAME", settings.default_service_name).strip()
        or settings.default_service_name,
        log_service_prefix=os.getenv("LOG_PULL_SERVICE_PREFIX", settings.default_service_prefix).strip()
        or settings.default_service_prefix,
        excluded_biztypes=_text_list(
            os.getenv("LOG_PULL_EXCLUDED_BIZTYPES", ""),
            settings.excluded_biztypes,
        ),
        log_days=max(1, int(os.getenv("LOG_PULL_DAYS", str(settings.default_log_days)))),
        log_tz_offset=int(os.getenv("LOG_PULL_TZ_OFFSET", str(settings.default_log_tz_offset))),
        source_repo=Path(raw_repo).resolve() if raw_repo else None,
        project_subdir=Path(raw_project_subdir) if raw_project_subdir else None,
        verify_commands=verify_commands,
        git_remote=os.getenv("AI_REPAIR_GIT_REMOTE", settings.default_git_remote).strip()
        or settings.default_git_remote,
        git_push=(
            os.getenv("AI_REPAIR_GIT_PUSH", str(settings.default_git_push).lower()).lower()
            == "true"
        ),
        review_mode=review_mode,
        max_attempts=max(
            1,
            int(os.getenv("AI_REPAIR_MAX_ATTEMPTS", str(settings.default_max_attempts))),
        ),
        claude=ClaudeConfig(
            auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN", ""),
            base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
            model=os.getenv("ANTHROPIC_MODEL", ""),
            default_haiku_model=os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", ""),
            default_opus_model=os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", ""),
            default_sonnet_model=os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", ""),
        ),
    )


def _text_list(raw: str, fallback: tuple[str, ...]) -> list[str]:
    """环境变量是 JSON 数组或逗号分隔；空值回退到 settings。"""
    if not raw.strip():
        return [item for item in fallback if item]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",")]
    if not isinstance(parsed, list):
        raise ValueError("日志排除列表必须是字符串数组")
    return [str(item) for item in parsed if str(item).strip()]


def index_days(
    # prefix：配置项用途见所属配置类及部署文档。
    prefix: str,
    # days：配置项用途见所属配置类及部署文档。
    days: int = settings.default_index_days,
    # tz_offset：配置项用途见所属配置类及部署文档。
    tz_offset: int = settings.default_index_tz_offset,
) -> list[str]:
    """返回最近的 ES 日志索引名；日期按业务时区取，避免 UTC 与本地日切错位。"""
    today = datetime.now(timezone(timedelta(hours=tz_offset)))
    return [
        f"{prefix}{(today - timedelta(days=offset)).strftime('%Y.%m.%d')}"
        for offset in range(days)
    ]


def create_es_client(config: RuntimeConfig) -> Any:
    """按配置创建 ES 客户端；调用发生在任务执行时而非模块导入时。"""
    from elasticsearch7 import Elasticsearch

    return Elasticsearch(config.log_es, timeout=settings.es_timeout_seconds)


def workflow_recursion_limit(*, max_attempts: int, task_count_hint: int = 1) -> int:
    """计算覆盖串行任务和有界回修的 LangGraph recursion limit。"""
    attempts = max(1, int(max_attempts))
    tasks = max(1, min(100, int(task_count_hint)))
    # 每个任务包含准备、Agent、门禁、回归、审查、交付和收尾，
    # 失败时还会经过 retry_prepare；固定 40 对多任务运行过低。
    per_task = 18 + attempts * 8
    return max(int(settings.graph_recursion_limit), 16 + tasks * per_task)
