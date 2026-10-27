"""修改前验收条件及基线复现；补丁变化不会改变验收标准。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import Field

from app.workflow.contracts import (
    AcceptanceAssertion,
    AcceptanceContract,
    BaselineReproduction,
)
from app.workflow.spec import FrozenModel
from app.workflow.validation import fingerprint


class AnalysisChecksInput(FrozenModel):
    generate_criteria: bool = True
    problem: dict[str, Any]
    baseline_dir: str
    baseline_manifest: dict[str, str]
    diagnosis: dict[str, Any]


def baseline_identity(directory: str, manifest: dict[str, str]) -> str:
    """核对完整基线文件集合，避免改动文件子集产生不同基线身份。"""
    root = Path(directory).resolve()
    if not root.is_dir() or not manifest:
        raise ValueError("BASELINE_MISSING: 缺少修复前源码基线")
    for relative, expected in manifest.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"BASELINE_CHANGED: 基线文件不存在或越界: {relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"BASELINE_CHANGED: 基线文件内容变化: {relative}")
    return fingerprint(manifest)


def build_acceptance_contract(data: AnalysisChecksInput) -> AcceptanceContract:
    """平台支持的结构化验收断言，在修改前固定。"""
    assertions = [
        AcceptanceAssertion(
            assertion_id="baseline",
            description="原始样本在基线复现原异常",
            method="baseline_replay",
            verifier="original_error",
            expected={"matches_original_error": True},
        ),
        AcceptanceAssertion(
            assertion_id="input",
            description="同一录制正文到达解析入口",
            method="fixed_replay",
            verifier="response_replay",
            expected={"input_delivered": True},
        ),
        AcceptanceAssertion(
            assertion_id="fixed",
            description="原异常被修复且返回符合框架业务协议",
            method="fixed_replay",
            verifier="response_replay",
            expected={"semantic_comparison_passed": True},
        ),
        AcceptanceAssertion(
            assertion_id="request",
            description="原请求通过框架入口并核对实际业务结果",
            method="live_request",
            verifier="server_dev.main",
            expected={"entrypoint": "server_dev.main", "business_passed": True},
        ),
    ]
    fields = {
        "problem_id": fingerprint(data.problem),
        "baseline_id": baseline_identity(data.baseline_dir, data.baseline_manifest),
        "diagnosis_id": fingerprint(data.diagnosis),
        "assertions": [item.model_dump(mode="json") for item in assertions] if data.generate_criteria else [],
    }
    return AcceptanceContract(contract_id=fingerprint(fields), **fields)


def validate_acceptance(state: dict[str, Any]) -> AcceptanceContract:
    """分别报告缺失、内容变化、问题变化和基线变化。"""
    raw = state.get("acceptance_contract")
    if not raw:
        raise ValueError("ACCEPTANCE_MISSING: 缺少修改前验收契约")
    contract = AcceptanceContract.model_validate(raw)
    fields = contract.model_dump(mode="json", exclude={"contract_id"})
    if fingerprint(fields) != contract.contract_id:
        raise ValueError("ACCEPTANCE_CHANGED: 验收契约内容身份不匹配")
    if fingerprint(state.get("problem") or {}) != contract.problem_id:
        raise ValueError("PROBLEM_CHANGED: 原始问题已变化")
    if (
        baseline_identity(state["baseline_dir"], state["baseline_manifest"])
        != contract.baseline_id
    ):
        raise ValueError("BASELINE_CHANGED: 验收契约与完整基线不匹配")
    if state.get("locate") and fingerprint(state["locate"]) != contract.diagnosis_id:
        raise ValueError("DIAGNOSIS_CHANGED: 定位结论已变化，需要重新定义验收")
    return contract


class BaselineReplayInput(FrozenModel):
    task_id: str
    carrier: str
    problem: dict[str, Any]
    baseline_dir: str
    baseline_manifest: dict[str, str]
    source_project_path: str
    ai_workspace_path: str
    acceptance_contract: AcceptanceContract
    candidates: list[str] = Field(default_factory=list)


def run_baseline_reproduction(ctx: Any, data: BaselineReplayInput) -> BaselineReproduction:
    """只运行未修改的代码，不把同一次执行猜作修复后结果。"""
    callback = ctx.services.metadata.get("independent_verify")
    if not callable(callback):
        raise ValueError("VERIFIER_MISSING: 基线重放验证器未配置")
    contract = validate_acceptance(data.model_dump(mode="json"))
    clean_source = (
        str(ctx.services.workspaces.project_root(ctx.services.source_repo))
        if ctx.services.workspaces and ctx.services.source_repo
        else ""
    )
    result = callback(
        {
            "task_id": data.task_id,
            "carrier": data.carrier,
            "normalized_log": data.problem,
            "baseline_dir": data.baseline_dir,
            "source_project_path": data.source_project_path,
            "clean_source_path": clean_source,
            "ai_workspace_path": data.ai_workspace_path,
            "changed_files": data.candidates or list(data.baseline_manifest),
            "baseline_only": True,
        }
    )
    path = "artifacts/baseline_reproduction.json"
    ctx.emit_artifact("baseline_reproduction", path, result)
    return BaselineReproduction(
        problem_id=contract.problem_id,
        baseline_id=contract.baseline_id,
        reason_code="missing_response" if result.get("replay_input_mode") == "missing_response" else "",
        status="passed" if result.get("passed") is True else "unverified",
        retryable=result.get("retryable") is True
        or result.get("replay_verdict") == "environment",
        reason=str(result.get("reason") or "基线复现未完成"),
        report_path=path,
    )
