"""全局业务配置中心与核心默认值。

遵循 KISS 原则：
1. 敏感配置项（Token、密码、ES 连接串）通过 Nacos 或私有 .env 注入；
2. 核心业务默认值在此处统一声明，外部可通过环境变量或 Nacos 覆盖；
3. 各子模块私有的底层技术实现常量（如超时时间、重试次数、截断字数）归位至对应模块内部，禁止作为全局配置外溢。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def parse_bool(value: str | None, default: bool = False) -> bool:
    """按统一口径解析布尔取值 (1/true/yes/on 视为真)。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。"""
    import os
    return parse_bool(os.getenv(name), default)


@dataclass(frozen=True)
class Settings:
    """系统全局核心业务设置。"""

    # 1. 项目标识与服务版本
    project_name: str = "py.ai.bug.fixer"
    service_name: str = "ai-repair-workbench"
    service_version: str = "1.0.0"

    # 2. 基础存储与目录标识
    runtime_dir_name: str = "runtime"
    source_mirror_dir_name: str = "source-repo"
    linux_log_base_dir: str = "/var/log/yqtrack"

    # 3. 日志拉取业务默认值
    default_service_name: str = "gather.py.track"
    default_service_prefix: str = "gather.py"
    default_log_hours: int = 24
    default_log_limit: int = 0
    default_log_tz_offset: int = 8

    # 4. 修复与审查业务策略
    default_review_mode: str = "agent"  # agent: AI 自动审查, human: 人工审查
    default_max_attempts: int = 3       # 单任务最大修复尝试总次数
    repair_branch_prefix: str = "ai-bug-tasks"
    default_git_remote: str = "origin"

    # 5. Web 控制台默认监听参数
    workbench_host: str = "0.0.0.0"
    workbench_api_port: int = 8000
    workbench_allow_insecure: bool = False
    workbench_cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    loopback_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

    # 6. Nacos 配置中心默认连接参数
    nacos_enabled: bool = True
    nacos_default_server_addr: str = "10.200.0.38:8848"
    nacos_default_namespace: str = "test-1"
    nacos_default_data_id: str = "ai-bug-fixer.json"
    nacos_default_group: str = "python"
    nacos_connect_timeout: int = 3


settings = Settings()

__all__ = ["ROOT", "Settings", "settings", "parse_bool", "env_bool"]
