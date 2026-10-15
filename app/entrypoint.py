"""服务入口。

完整执行走编译后的 LangGraph：``python -m app.entrypoint``。
单步调试不走这里，去各插件自己的 ``__main__``。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.infrastructure.config import load_config
from app.infrastructure.records.store import RecordStore
from app.infrastructure.runtime import close_sessions
from app.workflow.wiring import build_graph


def run_workflow(run_id: str | None = None) -> dict[str, Any]:
    """执行一次完整工作流。所有 carrier 任务复用同一个 AI 隔离工作区。"""
    config = load_config()
    if config.source_repo is None:
        raise RuntimeError("AI_REPAIR_SOURCE_REPO 未配置，禁止启动完整修复工作流")
    resolved = run_id or uuid.uuid4().hex
    store = RecordStore(config.runtime_dir)
    store.create_run(
        resolved,
        {
            "source_repo_path": str(config.source_repo),
            "project_subdir": str(config.project_subdir or ""),
            "log_service_name": config.log_service_name,
            "log_service_prefix": config.log_service_prefix,
            "log_excluded_messages": list(config.ignore_errors),
            "log_excluded_biztypes": list(config.excluded_biztypes),
            "log_days": config.log_days,
            "log_limit": config.limit,
        },
    )
    graph = build_graph(store=store)
    try:
        graph.invoke(
            {
                "schema_version": 2,
                "run_id": resolved,
                "source_repo_path": str(config.source_repo),
                "log_service_name": config.log_service_name,
            "log_service_prefix": config.log_service_prefix,
                "log_excluded_messages": list(config.ignore_errors),
                "log_excluded_biztypes": list(config.excluded_biztypes),
                "log_days": config.log_days,
                "log_limit": config.limit,
                "attempt": 1,
                "max_attempts": config.max_attempts,
                "review_mode": config.review_mode,
            },
            config={"configurable": {"thread_id": resolved}, "recursion_limit": 40},
        )
    finally:
        close_sessions()
    return {"run_id": resolved, "steps": [item.name for item in store.list_steps(resolved)]}


def main() -> None:
    """主入口下方的调试入口：跑整张图，打印本次执行的步骤记录名。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="调试完整工作流")
    parser.add_argument("--run-id", default="", help="指定执行号；缺省自动生成")
    args = parser.parse_args()
    print(json.dumps(run_workflow(args.run_id or None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = ["main", "run_workflow"]
