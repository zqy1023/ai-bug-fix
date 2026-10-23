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
    # runtime_dir_name：运行记录目录名称。
    runtime_dir_name: str = "runtime"
    # artifact_dir_name：产物目录名称。
    artifact_dir_name: str = ".ai-bug-tasks"
    # source_mirror_dir_name：远程源仓库在运行时目录下的镜像目录名。
    source_mirror_dir_name: str = "source-repo"
    # git_fetch_timeout_seconds：克隆与拉取源仓库的超时秒数。
    git_fetch_timeout_seconds: int = 300
    # default_service_name：默认日志服务名。
    default_service_name: str = "gather.py.track"
    # default_log_limit：默认日志条数上限。
    default_log_limit: int = 0
    # default_log_hours：默认滚动回溯小时数。
    default_log_hours: int = 12
    # default_log_tz_offset：日志时区偏移。
    default_log_tz_offset: int = 8
    # default_max_attempts：单任务最大尝试次数。
    default_max_attempts: int = 3
    # graph_retry_attempts：图节点瞬时错误重试次数。
    graph_retry_attempts: int = 2
    # graph_recursion_limit：图递归上限。
    graph_recursion_limit: int = 40
    # graph_max_tasks：递归上限计算时计入的最大任务数。
    graph_max_tasks: int = 100
    # graph_per_task_steps：单任务固定步骤数。
    graph_per_task_steps: int = 18
    # graph_per_attempt_steps：每次尝试追加的步骤数。
    graph_per_attempt_steps: int = 8
    # graph_overhead_steps：图级固定开销步骤数。
    graph_overhead_steps: int = 16
    # workflow_checkpoint_db_name：图检查点库文件名。
    workflow_checkpoint_db_name: str = "workflow-checkpoints.sqlite3"
    # workflow_checkpoint_timeout_seconds：检查点库连接等待秒数。
    workflow_checkpoint_timeout_seconds: int = 30
    # workflow_checkpoint_busy_timeout_ms：检查点库忙等待毫秒数。
    workflow_checkpoint_busy_timeout_ms: int = 30000
    # workbench_host：工作台服务监听地址。
    workbench_host: str = "0.0.0.0"
    # workbench_api_port：工作台服务监听端口。
    workbench_api_port: int = 8000
    # workbench_cors_origins：允许跨域访问的来源。
    workbench_cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    # workbench_control_db_name：工作台控制库文件名。
    workbench_control_db_name: str = "workbench-control.sqlite3"
    # workbench_sqlite_timeout_seconds：控制库连接等待秒数。
    workbench_sqlite_timeout_seconds: int = 10
    # workbench_sqlite_busy_timeout_ms：控制库忙等待毫秒数。
    workbench_sqlite_busy_timeout_ms: int = 10000
    # workbench_control_read_timeout_seconds：工作流读取控制信号的等待秒数。
    workbench_control_read_timeout_seconds: int = 1
    # workbench_page_size：列表接口默认分页大小。
    workbench_page_size: int = 20
    # workbench_max_page_size：列表接口最大分页大小。
    workbench_max_page_size: int = 100
    # workbench_max_attempts：接口允许的最大尝试次数。
    workbench_max_attempts: int = 5
    # workbench_max_log_hours：单次运行允许回溯的最大小时数。
    workbench_max_log_hours: int = 720
    # workbench_prompt_snapshot：是否把完整提示词快照写入任务审计目录。
    workbench_prompt_snapshot: bool = True
    # workbench_prompt_max_chars：保留旧配置兼容；完整审计快照不再按此值截断。
    workbench_prompt_max_chars: int = 120000
    # dashboard_recent_hours：工作台“最近”统计窗口小时数。
    dashboard_recent_hours: int = 24
    # dashboard_trend_days：工作台趋势统计天数。
    dashboard_trend_days: int = 7
    # repair_claims_db_name：修复资格库文件名。
    repair_claims_db_name: str = "repair-claims.sqlite3"
    # repair_claims_timeout_seconds：修复资格库连接等待秒数。
    repair_claims_timeout_seconds: int = 10
    # repair_claims_busy_timeout_ms：修复资格库忙等待毫秒数。
    repair_claims_busy_timeout_ms: int = 10000
    # repair_claim_ttl_hours：修复资格有效期小时数。
    repair_claim_ttl_hours: int = 24
    # default_git_remote：默认 Git 远程名。
    default_git_remote: str = "origin"
    # repair_branch_prefix：修复分支前缀。
    repair_branch_prefix: str = "ai-bug-tasks"
    # default_git_push：是否默认推送。
    default_git_push: bool = False
    # default_review_mode：默认审查模式。
    default_review_mode: str = "agent"
    # default_ignore_errors：默认忽略错误清单。
    default_ignore_errors: tuple[str, ...] = ("redis.exceptions.ConnectionError",)
    # Kibana 看板口径：前缀加精确 service_name，避免 gather.py 其他服务混入。
    default_service_prefix: str = "gather.py"
    # excluded_biztypes：排除的业务类型。
    excluded_biztypes: tuple[str, ...] = (
        "framework.controllers.cache_controller",
        "framework.task_manager.result_sender",
    )
    # repair_branch_pattern：修复分支名校验规则。
    # 一级目录是 biz_type，可能包含中文；\w 在 Unicode 模式下覆盖中英文。
    repair_branch_pattern: re.Pattern[str] = re.compile(
        r"^ai-bug-tasks/[\w.-]+(?:/[\w.-]+)?$", re.UNICODE
    )
    # max_commit_comment_chars：提交说明最大字符数。
    max_commit_comment_chars: int = 800
    # max_diff_summary_chars：差异摘要最大字符数。
    max_diff_summary_chars: int = 8000
    # evidence_max_scan_files：证据扫描文件数上限。
    evidence_max_scan_files: int = 300
    # evidence_max_read_bytes：单文件读取字节上限。
    evidence_max_read_bytes: int = 524288
    # evidence_max_symbols：单文件符号数量上限。
    evidence_max_symbols: int = 32
    # evidence_query_value_chars：查询字段字符上限。
    evidence_query_value_chars: int = 600
    # evidence_extracted_value_chars：提取证据字符上限。
    evidence_extracted_value_chars: int = 500
    # max_sessions：Agent 会话数量上限。
    max_sessions: int = 8
    # es_timeout_seconds：ES 请求超时秒数。
    es_timeout_seconds: int = 30
    # es_index_prefix：ES 日志索引名前缀。
    es_index_prefix: str = "logstash-gather-"
    # es_scan_size：ES 扫描批次大小。
    es_scan_size: int = 1000
    # es_scroll：ES 扫描上下文保留时长。
    es_scroll: str = "2m"
    # es_index_date_format：ES 索引名日期格式。
    es_index_date_format: str = "%Y.%m.%d"
    # default_excluded_messages：日志采集基础设施噪声，不参与业务错误识别。
    default_excluded_messages: tuple[str, ...] = ("redis.exceptions.ConnectionError",)
    # command_timeout_seconds：固定验证命令超时秒数。
    command_timeout_seconds: int = 600
    # replay_timeout_seconds：response 重放超时秒数。
    replay_timeout_seconds: int = 30
    # replay_python：跑业务代码的重放解释器路径。必须是装了业务依赖（与线上环境
    # 一致）的解释器，否则重放会在 import 阶段失败。留空表示用当前解释器。
    # 部署时用 AI_REPAIR_REPLAY_PYTHON 覆盖。
    replay_python: str = ""
    # replay_output_chars：重放成功输出保留字符数。
    replay_output_chars: int = 500
    # regression_tail_chars：回归命令输出保留的尾部字符数。
    regression_tail_chars: int = 8000
    # record_json_max_bytes：单条步骤记录字节上限。
    record_json_max_bytes: int = 1 * 1024 * 1024
    # check_changes_max_output_items：门禁记录中列表项上限。
    check_changes_max_output_items: int = 200
    # git_audit_max_bytes：交付时单个审计产物字节上限。
    git_audit_max_bytes: int = 512 * 1024
    # commit_analysis_chars：提交说明中修复分析保留字符数。
    commit_analysis_chars: int = 800
    # workspace_read_max_bytes：读取 carrier 文本单文件字节上限。
    workspace_read_max_bytes: int = 512 * 1024
    # workspace_retention_days：终态执行的工作区保留天数。
    workspace_retention_days: int = 7
    # delivery_workspace_ttl_hours：交付临时克隆的最长保留时间。
    delivery_workspace_ttl_hours: int = 24
    # workspace_gc_enabled：是否自动回收过期工作区。
    workspace_gc_enabled: bool = True
    # workspace_gc_interval_seconds：服务运行期间定时回收间隔。
    workspace_gc_interval_seconds: int = 3600
    # artifact_mode：compact 保留必要证据；debug 额外保存完整提示词和事件。
    artifact_mode: str = "compact"
    # diff_max_lines_per_file：单文件差异保留行数。
    diff_max_lines_per_file: int = 120
    # diff_max_line_chars：单行差异保留字符数。
    diff_max_line_chars: int = 400
    # diff_max_files：差异文件数量上限。
    diff_max_files: int = 20
    # diff_max_file_bytes：参与差异比较的单文件字节上限。
    diff_max_file_bytes: int = 2 * 1024 * 1024
    # diff_cache_size：差异与哈希缓存条目上限。
    diff_cache_size: int = 512
    # claude_event_text_chars：Agent 事件正文保留字符数。
    claude_event_text_chars: int = 2000
    # claude_event_value_chars：Agent 事件字段值保留字符数。
    claude_event_value_chars: int = 200
    # claude_event_tool_text_chars：Agent 工具结果保留字符数。
    claude_event_tool_text_chars: int = 800
    # claude_max_events：单个模型调用保留的事件数。
    claude_max_events: int = 200
    # claude_debug_log_max_bytes：Agent 调试日志轮转字节上限。
    claude_debug_log_max_bytes: int = 5 * 1024 * 1024
    # claude_default_max_turns：未注入配置时的回合预算。
    claude_default_max_turns: int = 30
    # claude_default_max_tool_calls：未注入配置时的工具调用预算。
    claude_default_max_tool_calls: int = 32
    # claude_default_timeout_seconds：未注入配置时的 Agent 超时秒数。
    claude_default_timeout_seconds: int = 900
    # claude_thread_join_timeout_seconds：关闭会话线程的等待秒数。
    claude_thread_join_timeout_seconds: int = 30
    # agent_progress：是否记录 Agent 进度。
    agent_progress: bool = True
    # agent_archive：是否归档 Agent 事件。
    agent_archive: bool = True
    # agent_archive_path：Agent 事件归档路径。
    agent_archive_path: Path = ROOT / "runtime" / "agent-hooks.jsonl"


settings = Settings()

__all__ = ["ROOT", "Settings", "settings"]
