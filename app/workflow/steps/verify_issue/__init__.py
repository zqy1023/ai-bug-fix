"""独立验证原始异常是否已修复。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, RecordView, StepSpec
from app.workflow.steps.verify_issue.models import VerifyInput, VerifyOutput
from app.workflow.steps.verify_issue.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    problem = dict(state.get("problem") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "problem_key": str(task.get("problem_key") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "source_project_path": str(state.get("source_project_path") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "normalized_log": problem,  # 传递完整的 problem 数据用于 response 重放
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    unverified = str(output.get("status") or "").lower() == "unverified"
    return {
        "verify_passed": bool(output["passed"]),
        "verify_result": output,
        "business_verification_status": "unverified" if unverified else ("verified" if output["passed"] else "failed"),
        # 未验证允许继续到业务回归和 AI 审查，但绝不当作业务通过。
        "last_result": "passed" if unverified else ("passed" if output["passed"] else "failed"),
    }


SPEC = StepSpec(
    name="verify_issue",
    title="业务问题验证",
    runner=run,
    input_model=VerifyInput,
    output_model=VerifyOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("plan_validation", "task"),),
    next_step="run_regression",
    # 落盘投影：重放日志正文（结果 JSON / stdout / 命令）原文只落在
    # artifacts/verification_report.json。步骤记录里留同一份会被审计投影按
    # 600 字截断，既占体积又给前端一份残缺副本，判定只需要比较结论。
    output_record=RecordView(
        trim=(
            (
                "replay",
                ("replay_verdict", "replay_input_mode", "replay_comparison", "replay_full_output"),
            ),
        )
    ),
)

__all__ = ["SPEC"]
