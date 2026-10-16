"""服务入口。

完整执行走编译后的 LangGraph：``python -m app.entrypoint``。
单步调试不走这里，去各插件自己的 ``__main__``。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from app.infrastructure.config import load_config
from app.infrastructure.records.store import RecordStore
from app.infrastructure.runtime import close_sessions


def prepare_run(
    run_id: str | None = None, *, overrides: dict[str, Any] | None = None
) -> tuple[str, Any, RecordStore]:
    """同步创建可查询的 Run 元数据；供 API 接受请求前与后台执行共用。"""
    config = load_config()
    options = dict(overrides or {})
    config = replace(
        config,
        log_days=int(options.get("log_days", config.log_days)),
        limit=(config.limit if int(options.get("log_limit", 0)) == 0 else int(options["log_limit"])),
        review_mode=str(options.get("review_mode", config.review_mode)),
        max_attempts=int(options.get("max_attempts", config.max_attempts)),
    )
    if not 1 <= config.log_days <= 30 or not 1 <= config.max_attempts <= 5 or config.limit < 0:
        raise ValueError("执行参数超出允许范围")
    if config.review_mode not in ("agent", "human"):
        raise ValueError("review_mode 只能是 agent 或 human")
    if config.source_repo is None:
        raise RuntimeError("AI_REPAIR_SOURCE_REPO 未配置，禁止启动完整修复工作流")

    resolved = run_id or uuid.uuid4().hex
    store = RecordStore(config.runtime_dir)
    metadata = {
        "source_repo_path": str(config.source_repo),
        "project_subdir": str(config.project_subdir or ""),
        "log_service_name": config.log_service_name,
        "log_service_prefix": config.log_service_prefix,
        "log_excluded_messages": list(config.ignore_errors),
        "log_excluded_biztypes": list(config.excluded_biztypes),
        "log_days": config.log_days,
        "log_limit": config.limit,
        "review_mode": config.review_mode,
        "max_attempts": config.max_attempts,
    }
    run_json = store.run_dir(resolved) / "run.json"
    if run_json.is_file():
        # 预创建记录由 API 与后台共享；仅允许相同配置继续执行，绝不覆盖历史 Run。
        existing = store.load_run(resolved)
        mismatches = [
            key for key, value in metadata.items()
            if existing.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(
                f"执行 {resolved} 已存在，但配置不匹配：{', '.join(mismatches)}"
            )
    else:
        store.create_run(resolved, metadata)
    return resolved, config, store


def run_workflow(run_id: str | None = None, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行一次完整工作流；每个 carrier 任务使用自己的隔离工作区。"""
    resolved, config, store = prepare_run(run_id, overrides=overrides)
    from app.workflow.wiring import build_graph

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
    """命令行执行整张工作流。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="执行完整修复工作流")
    parser.add_argument("--run-id", default="", help="指定执行号；缺省自动生成")
    args = parser.parse_args()
    print(json.dumps(run_workflow(args.run_id or None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = ["main", "prepare_run", "run_workflow"]
