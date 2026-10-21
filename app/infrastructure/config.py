"""新工作流运行配置。"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
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
    # log_hours：日志回溯小时数。
    log_hours: int
    # log_tz_offset：日志时区偏移。
    log_tz_offset: int
    # es_timeout_seconds：ES 请求超时秒数。
    es_timeout_seconds: int
    # es_index_prefix：ES 日志索引名前缀。
    es_index_prefix: str
    # es_scan_size：ES 扫描批次大小。
    es_scan_size: int
    # es_scroll：ES 扫描上下文保留时长。
    es_scroll: str
    # excluded_messages：日志采集基础设施噪声清单。
    excluded_messages: list[str]
    # command_timeout_seconds：固定验证命令超时秒数。
    command_timeout_seconds: int
    # replay_timeout_seconds：response 重放超时秒数。
    replay_timeout_seconds: int
    # workbench_sqlite_timeout_seconds：控制库连接等待秒数。
    workbench_sqlite_timeout_seconds: int
    # workbench_sqlite_busy_timeout_ms：控制库忙等待毫秒数。
    workbench_sqlite_busy_timeout_ms: int
    # workbench_host：工作台服务监听地址。
    workbench_host: str
    # workbench_api_port：工作台服务监听端口。
    workbench_api_port: int
    # workbench_cors_origins：允许跨域访问的来源。
    workbench_cors_origins: list[str]
    # source_repo：源 Git 仓库路径；URL 模式下指向运行时目录里的本地镜像。
    source_repo: Path | None
    # source_url：远程 Git 仓库 URL，配置后由系统克隆并拉取。
    source_url: str
    # project_subdir：仓库内项目子目录。
    project_subdir: Path | None
    # verify_commands：验证命令数组。
    verify_commands: list[list[str]]
    # git_remote：Git 远程名。
    git_remote: str
    # git_username：HTTPS 拉取/推送用户名；不进入 repr。
    git_username: str = field(repr=False)
    # git_password：HTTPS 拉取/推送密码或令牌；不进入 repr。
    git_password: str = field(repr=False)
    # git_fetch_timeout_seconds：克隆与拉取源仓库的超时秒数。
    git_fetch_timeout_seconds: int
    # git_push：是否自动推送。
    git_push: bool
    # review_mode：审查模式。
    review_mode: str
    # max_attempts：单任务最大修复尝试次数。
    max_attempts: int
    # workspace_retention_days：终态执行工作区保留天数。
    workspace_retention_days: int
    # delivery_workspace_ttl_hours：交付临时克隆保留小时数。
    delivery_workspace_ttl_hours: int
    # workspace_gc_enabled：启动时是否回收过期工作区。
    workspace_gc_enabled: bool
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
    raw_excluded_messages = os.getenv("LOG_PULL_EXCLUDED_MESSAGES", "")
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
    raw_source_url = os.getenv("AI_REPAIR_SOURCE_URL", "").strip()
    raw_git_username = os.getenv("AI_REPAIR_GIT_USERNAME", "").strip()
    raw_git_password = os.getenv("AI_REPAIR_GIT_PASSWORD", "")
    if raw_git_password and not raw_git_username:
        raise ValueError("设置了 AI_REPAIR_GIT_PASSWORD 时必须同时设置 AI_REPAIR_GIT_USERNAME")
    raw_project_subdir = os.getenv("AI_REPAIR_PROJECT_SUBDIR", "").strip()
    raw_runtime_dir = os.getenv("AI_REPAIR_RUNTIME_DIR", "").strip()
    runtime_dir = (
        Path(raw_runtime_dir).resolve()
        if raw_runtime_dir
        else root / settings.runtime_dir_name
    )
    # URL 模式下运行时目录中维护受控镜像；本地路径仅用于离线/开发模式。
    source_repo = (
        runtime_dir / settings.source_mirror_dir_name
        if raw_source_url
        else (Path(raw_repo).resolve() if raw_repo else None)
    )
    review_mode = os.getenv("AI_REPAIR_REVIEW_MODE", settings.default_review_mode).strip().lower()
    if review_mode not in ("agent", "human"):
        raise ValueError("AI_REPAIR_REVIEW_MODE 只能是 agent 或 human")
    return RuntimeConfig(
        log_es=os.getenv("LOG_ES", ""),
        runtime_dir=runtime_dir,
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
        log_hours=max(1, int(os.getenv("LOG_PULL_HOURS") or str(int(os.getenv("LOG_PULL_DAYS", "1")) * 24))),
        log_tz_offset=int(os.getenv("LOG_PULL_TZ_OFFSET", str(settings.default_log_tz_offset))),
        es_timeout_seconds=max(
            1,
            int(os.getenv("ES_TIMEOUT_SECONDS", str(settings.es_timeout_seconds))),
        ),
        es_index_prefix=os.getenv("ES_INDEX_PREFIX", settings.es_index_prefix).strip()
        or settings.es_index_prefix,
        es_scan_size=max(1, int(os.getenv("ES_SCAN_SIZE", str(settings.es_scan_size)))),
        es_scroll=os.getenv("ES_SCROLL", settings.es_scroll).strip() or settings.es_scroll,
        excluded_messages=_text_list(raw_excluded_messages, settings.default_excluded_messages),
        command_timeout_seconds=max(
            1,
            int(
                os.getenv(
                    "AI_REPAIR_COMMAND_TIMEOUT_SECONDS",
                    str(settings.command_timeout_seconds),
                )
            ),
        ),
        replay_timeout_seconds=max(
            1,
            int(
                os.getenv(
                    "AI_REPAIR_REPLAY_TIMEOUT_SECONDS",
                    str(settings.replay_timeout_seconds),
                )
            ),
        ),
        workbench_sqlite_timeout_seconds=max(
            1,
            int(
                os.getenv(
                    "WORKBENCH_SQLITE_TIMEOUT_SECONDS",
                    str(settings.workbench_sqlite_timeout_seconds),
                )
            ),
        ),
        workbench_sqlite_busy_timeout_ms=max(
            1,
            int(
                os.getenv(
                    "WORKBENCH_SQLITE_BUSY_TIMEOUT_MS",
                    str(settings.workbench_sqlite_busy_timeout_ms),
                )
            ),
        ),
        workbench_host=os.getenv("WORKBENCH_BIND_HOST", settings.workbench_host).strip()
        or settings.workbench_host,
        workbench_api_port=max(
            1,
            int(os.getenv("WORKBENCH_BIND_PORT", str(settings.workbench_api_port))),
        ),
        workbench_cors_origins=_text_list(
            os.getenv("WORKBENCH_CORS_ORIGINS", ""),
            settings.workbench_cors_origins,
        ),
        source_repo=source_repo,
        source_url=raw_source_url,
        project_subdir=Path(raw_project_subdir) if raw_project_subdir else None,
        verify_commands=verify_commands,
        git_remote=os.getenv("AI_REPAIR_GIT_REMOTE", settings.default_git_remote).strip()
        or settings.default_git_remote,
        git_username=raw_git_username,
        git_password=raw_git_password,
        git_fetch_timeout_seconds=max(
            1,
            int(
                os.getenv(
                    "AI_REPAIR_GIT_FETCH_TIMEOUT_SECONDS",
                    str(settings.git_fetch_timeout_seconds),
                )
            ),
        ),
        git_push=(
            os.getenv("AI_REPAIR_GIT_PUSH", str(settings.default_git_push).lower()).lower()
            == "true"
        ),
        review_mode=review_mode,
        max_attempts=max(
            1,
            int(os.getenv("AI_REPAIR_MAX_ATTEMPTS", str(settings.default_max_attempts))),
        ),
        workspace_retention_days=max(
            0,
            int(os.getenv(
                "AI_REPAIR_WORKSPACE_RETENTION_DAYS",
                str(settings.workspace_retention_days),
            )),
        ),
        delivery_workspace_ttl_hours=max(
            0,
            int(os.getenv(
                "AI_REPAIR_DELIVERY_WORKSPACE_TTL_HOURS",
                str(settings.delivery_workspace_ttl_hours),
            )),
        ),
        workspace_gc_enabled=(
            os.getenv(
                "AI_REPAIR_WORKSPACE_GC_ENABLED",
                str(settings.workspace_gc_enabled).lower(),
            ).lower() == "true"
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
        f"{prefix}{(today - timedelta(days=offset)).strftime(settings.es_index_date_format)}"
        for offset in range(days)
    ]


def create_es_client(config: RuntimeConfig) -> Any:
    """按配置创建 ES 客户端；调用发生在任务执行时而非模块导入时。"""
    from elasticsearch7 import Elasticsearch

    # 节点层已经有有界重试，禁止传输层再次把一次超时放大为多轮等待。
    return Elasticsearch(config.log_es, timeout=config.es_timeout_seconds, max_retries=0, retry_on_timeout=False)


def workflow_recursion_limit(*, max_attempts: int, task_count_hint: int = 1) -> int:
    """计算覆盖串行任务和有界回修的 LangGraph recursion limit。"""
    attempts = max(1, int(max_attempts))
    tasks = max(1, min(settings.graph_max_tasks, int(task_count_hint)))
    # 每个任务包含准备、Agent、门禁、回归、审查、交付和收尾，
    # 失败时还会经过 retry_prepare；固定 40 对多任务运行过低。
    per_task = settings.graph_per_task_steps + attempts * settings.graph_per_attempt_steps
    return max(
        int(settings.graph_recursion_limit),
        settings.graph_overhead_steps + tasks * per_task,
    )
