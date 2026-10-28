"""新工作流运行配置。"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv

from config.nacos import load_nacos_config, nacos_bool, nacos_get
from config.settings import settings


class RunParameters(TypedDict):
    """新建执行、持久化恢复和工作台共用的运行参数。"""

    log_hours: int
    log_limit: int
    review_mode: str
    max_attempts: int


def log_hours_from_values(values: Mapping[str, Any], *, default: int) -> int:
    """只读取当前小时配置，缺失时使用有效默认值。"""
    if values.get("log_hours") is not None:
        return int(values["log_hours"])
    return default


@dataclass(frozen=True)
class ClaudeConfig:
    """Claude Agent SDK 网关和模型配置。"""

    # auth_token：模型服务认证令牌。
    auth_token: str
    # base_url：模型服务 API 基础地址。
    base_url: str
    # model：默认模型标识。
    model: str
    # models：可选模型声明；实际可用性仍以 provider /v1/models 探测为准。
    models: tuple[str, ...] = ()

    def as_env(self) -> dict[str, str]:
        """返回 SDK 所需的非空环境变量。"""
        values = {
            "ANTHROPIC_AUTH_TOKEN": self.auth_token,
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_MODELS": ",".join(self.models),
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
    # workbench_sqlite_timeout_seconds：控制库连接等待秒数。
    workbench_sqlite_timeout_seconds: int
    # workbench_sqlite_busy_timeout_ms：控制库忙等待毫秒数。
    workbench_sqlite_busy_timeout_ms: int
    # workbench_host：工作台服务监听地址。
    workbench_host: str
    # workbench_allow_insecure：未配置 token 时是否允许绑定非回环地址。
    workbench_allow_insecure: bool
    # workbench_prompt_snapshot：是否把完整提示词快照写入任务审计目录。
    workbench_prompt_snapshot: bool
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
    # git_remote：Git 远程名。
    git_remote: str
    # git_username：HTTPS 拉取/推送用户名；不进入 repr。
    git_username: str = field(repr=False)
    # git_password：HTTPS 拉取/推送密码或令牌；不进入 repr。
    git_password: str = field(repr=False)
    # git_fetch_timeout_seconds：已有镜像拉取与检出操作的超时秒数。
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
    # workspace_gc_enabled：是否自动回收过期工作区。
    workspace_gc_enabled: bool
    # claude：模型网关配置。
    claude: ClaudeConfig
    # workspace_gc_interval_seconds：服务运行期间定时回收间隔。
    workspace_gc_interval_seconds: int = 3600
    # 首次克隆单独限时，避免大仓库反复在 fetch 的短超时内重下。
    git_clone_timeout_seconds: int = 1800
    # git_committer_name：修复分支提交者名；容器内没有全局 git 身份，缺省会提交失败。
    git_committer_name: str = "ai-bug-fixer"
    # git_committer_email：修复分支提交者邮箱。
    git_committer_email: str = "ai-bug-fixer@localhost"
    @property
    def workspace_root(self) -> Path:
        """共享 AI 工作区、验证和回归工作区的根目录。"""
        return self.runtime_dir / "workspaces"

    def agent_env(self) -> dict[str, str]:
        """返回生产 agent 配置与 provider 共用的环境变量。"""
        return {**os.environ, **self.claude.as_env()}

    def run_parameters(self, values: Mapping[str, Any] | None = None) -> RunParameters:
        """已保存或显式传入的值优先；缺失字段从当前运行配置补齐，保留零条数上限。"""
        values = values or {}
        return {
            "log_hours": log_hours_from_values(values, default=self.log_hours),
            "log_limit": int(values["log_limit"]) if values.get("log_limit") is not None else self.limit,
            "review_mode": str(values["review_mode"]) if values.get("review_mode") is not None else self.review_mode,
            "max_attempts": int(values["max_attempts"]) if values.get("max_attempts") is not None else self.max_attempts,
        }


def load_config() -> RuntimeConfig:
    """从项目根目录 `.env` 加载运行配置。"""
    root = Path(__file__).resolve().parents[2]
    # 环境变量优先于 .env 文件：支持容器化部署和测试隔离。
    load_dotenv(root / ".env", override=False)
    raw_ignore = nacos_get("LOG_PULL_IGNORE_ERRORS", "")
    raw_excluded_messages = nacos_get("LOG_PULL_EXCLUDED_MESSAGES", "")
    raw_repo = nacos_get("AI_REPAIR_SOURCE_REPO", "").strip()
    raw_source_url = nacos_get("AI_REPAIR_SOURCE_URL", "").strip()
    raw_git_username = nacos_get("AI_REPAIR_GIT_USERNAME", "").strip()
    raw_git_password = nacos_get("AI_REPAIR_GIT_PASSWORD", "")
    if raw_git_password and not raw_git_username:
        raise ValueError("设置了 AI_REPAIR_GIT_PASSWORD 时必须同时设置 AI_REPAIR_GIT_USERNAME")
    raw_project_subdir = nacos_get("AI_REPAIR_PROJECT_SUBDIR", "").strip()
    raw_runtime_dir = nacos_get("AI_REPAIR_RUNTIME_DIR", "").strip()
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
    review_mode = nacos_get("AI_REPAIR_REVIEW_MODE", settings.default_review_mode).strip().lower()
    if review_mode not in ("agent", "human"):
        raise ValueError("AI_REPAIR_REVIEW_MODE 只能是 agent 或 human")
    # 日志窗口仅接受小时配置。
    raw_log_hours = nacos_get("LOG_PULL_HOURS", "").strip()
    log_hours = log_hours_from_values(
        {"log_hours": raw_log_hours or None},
        default=settings.default_log_hours,
    )
    return RuntimeConfig(
        log_es=nacos_get("LOG_ES", ""),
        runtime_dir=runtime_dir,
        ignore_errors=_text_list(raw_ignore, ("redis.exceptions.ConnectionError",)),
        limit=int(nacos_get("LOG_PULL_LIMIT", str(settings.default_log_limit))),
        log_service_name=nacos_get("LOG_PULL_SERVICE_NAME", settings.default_service_name).strip()
        or settings.default_service_name,
        log_service_prefix=nacos_get("LOG_PULL_SERVICE_PREFIX", settings.default_service_prefix).strip()
        or settings.default_service_prefix,
        excluded_biztypes=_text_list(
            nacos_get("LOG_PULL_EXCLUDED_BIZTYPES", ""),
            ("framework.controllers.cache_controller", "framework.task_manager.result_sender"),
        ),
        log_hours=max(1, log_hours),
        log_tz_offset=int(nacos_get("LOG_PULL_TZ_OFFSET", str(settings.default_log_tz_offset))),
        es_timeout_seconds=max(
            1,
            int(nacos_get("ES_TIMEOUT_SECONDS", "30")),
        ),
        es_index_prefix=nacos_get("ES_INDEX_PREFIX", "logstash-gather-").strip() or "logstash-gather-",
        es_scan_size=max(1, int(nacos_get("ES_SCAN_SIZE", "1000"))),
        es_scroll=nacos_get("ES_SCROLL", "2m").strip() or "2m",
        excluded_messages=_text_list(raw_excluded_messages, ("redis.exceptions.ConnectionError",)),
        command_timeout_seconds=max(
            1,
            int(
                nacos_get(
                    "AI_REPAIR_COMMAND_TIMEOUT_SECONDS",
                    "600",
                )
            ),
        ),
        workbench_sqlite_timeout_seconds=max(
            1,
            int(
                nacos_get(
                    "WORKBENCH_SQLITE_TIMEOUT_SECONDS",
                    "10",
                )
            ),
        ),
        workbench_sqlite_busy_timeout_ms=max(
            1,
            int(
                nacos_get(
                    "WORKBENCH_SQLITE_BUSY_TIMEOUT_MS",
                    "10000",
                )
            ),
        ),
        workbench_host=nacos_get("WORKBENCH_BIND_HOST", settings.workbench_host).strip()
        or settings.workbench_host,
        # 在 load_dotenv 之后读取，保证 .env 与进程环境使用同一优先级规则。
        workbench_allow_insecure=nacos_bool(
            "WORKBENCH_ALLOW_INSECURE", settings.workbench_allow_insecure
        ),
        # 提示词快照保存的是未脱敏原文，必须留一个运行期开关：此前这个开关只
        # 存在于 settings 里（进程启动后固化成常量），部署时无法关闭。
        workbench_prompt_snapshot=nacos_bool(
            "WORKBENCH_PROMPT_SNAPSHOT", True
        ),
        workbench_api_port=max(
            1,
            int(nacos_get("WORKBENCH_BIND_PORT", str(settings.workbench_api_port))),
        ),
        workbench_cors_origins=_text_list(
            nacos_get("WORKBENCH_CORS_ORIGINS", ""),
            settings.workbench_cors_origins,
        ),
        source_repo=source_repo,
        source_url=raw_source_url,
        project_subdir=Path(raw_project_subdir) if raw_project_subdir else None,
        git_remote=nacos_get("AI_REPAIR_GIT_REMOTE", settings.default_git_remote).strip()
        or settings.default_git_remote,
        git_username=raw_git_username,
        git_password=raw_git_password,
        git_clone_timeout_seconds=max(
            30,
            int(
                nacos_get(
                    "AI_REPAIR_GIT_CLONE_TIMEOUT_SECONDS",
                    "1800",
                )
            ),
        ),
        git_fetch_timeout_seconds=max(
            1,
            int(
                nacos_get(
                    "AI_REPAIR_GIT_FETCH_TIMEOUT_SECONDS",
                    "300",
                )
            ),
        ),
        git_push=nacos_bool("AI_REPAIR_GIT_PUSH", False),
        review_mode=review_mode,
        max_attempts=max(
            1,
            int(nacos_get("AI_REPAIR_MAX_ATTEMPTS", str(settings.default_max_attempts))),
        ),
        workspace_retention_days=max(
            0,
            int(nacos_get(
                "AI_REPAIR_WORKSPACE_RETENTION_DAYS",
                "7",
            )),
        ),
        delivery_workspace_ttl_hours=max(
            0,
            int(nacos_get(
                "AI_REPAIR_DELIVERY_WORKSPACE_TTL_HOURS",
                "24",
            )),
        ),
        workspace_gc_enabled=nacos_bool(
            "AI_REPAIR_WORKSPACE_GC_ENABLED", True
        ),
        workspace_gc_interval_seconds=max(1, int(nacos_get(
            "AI_REPAIR_WORKSPACE_GC_INTERVAL_SECONDS", "3600",
        ))),
        # 提交者身份与代理地址统一遵循：Nacos > 进程环境 > .env > Settings 默认值。
        git_committer_name=nacos_get("GIT_COMMITTER_NAME", "ai-bug-fixer").strip()
        or "ai-bug-fixer",
        git_committer_email=nacos_get("GIT_COMMITTER_EMAIL", "ai-bug-fixer@localhost").strip()
        or "ai-bug-fixer@localhost",
        claude=ClaudeConfig(
            auth_token=nacos_get("ANTHROPIC_AUTH_TOKEN", ""),
            base_url=nacos_get("ANTHROPIC_BASE_URL", ""),
            model=nacos_get("ANTHROPIC_MODEL", ""),
            models=tuple(
                item.strip()
                for item in nacos_get("ANTHROPIC_MODELS", "").split(",")
                if item.strip()
            ),
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


def index_days(prefix: str, days: int, tz_offset: int) -> list[str]:
    """返回最近的 ES 日志索引名；日期按业务时区取，避免 UTC 与本地日切错位。"""
    today = datetime.now(timezone(timedelta(hours=tz_offset)))
    return [
        f"{prefix}{(today - timedelta(days=offset)).strftime('%Y.%m.%d')}"
        for offset in range(days)
    ]


def create_es_client(config: RuntimeConfig) -> Any:
    """按配置创建 ES 客户端；调用发生在任务执行时而非模块导入时。"""
    from elasticsearch7 import Elasticsearch

    # 节点层已经有有界重试，禁止传输层再次把一次超时放大为多轮等待。
    return Elasticsearch(config.log_es, timeout=config.es_timeout_seconds, max_retries=0, retry_on_timeout=False)


def workflow_recursion_limit(
    *,
    max_attempts: int,
    task_count_hint: int = 1,
    task_count: int | None = None,
) -> int:
    """计算覆盖串行任务和有界回修的 LangGraph recursion limit。"""
    hint = task_count if task_count is not None else task_count_hint
    attempts = max(1, int(max_attempts))
    tasks = max(1, min(100, int(hint)))
    # 每个任务包含准备、Agent、门禁、回归、审查、交付和收尾，
    # 失败时还会经过 retry_prepare；固定 40 对多任务运行过低。
    per_task = 18 + attempts * 8
    return max(
        int(40),
        16 + tasks * per_task,
    )
