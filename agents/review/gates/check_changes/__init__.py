"""确定性范围、路径、语法树和敏感信息门禁。"""

from __future__ import annotations

from typing import Any

from core.contracts import RecordView, StepSpec
from agents.review.gates.check_changes.models import CheckInput, CheckOutput
from agents.review.gates.check_changes.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "carrier": str(task.get("carrier") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": list(state.get("changed_files") or []),
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 平台扫描的实际差异是下游补丁身份的唯一来源。
    # 内部工具被取消或异常中断时输出的是错误载荷，此时不覆盖差异身份。
    if "changed_files" not in output:
        return {}
    return {
        "gate_result": output,
        "changed_files": list(output.get("changed_files") or []),
    }


SPEC = StepSpec(
    name="check_changes",
    title="确定性检查",
    runner=run,
    input_model=CheckInput,
    output_model=CheckOutput,
    input_builder=_input,
    output_patch=_patch,
    # 落盘投影：baseline_manifest 是"carrier 全部文件 × 哈希"的门禁中间态，
    # 逐文件证据里size/sha256/nlink/secret_samples/ast/dangerous_calls 也都是
    # 过程量。判定要看的只有改动量与问题清单。
    input_record=RecordView(keep=("carrier", "changed_files", "baseline_dir")),
    output_record=RecordView(
        trim=(
            (
                "files",
                (
                    "path",
                    "in_scope",
                    "exists",
                    "status",
                    "added",
                    "removed",
                    "problems",
                ),
            ),
        )
    ),
)

__all__ = ["SPEC"]
