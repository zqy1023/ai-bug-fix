import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.contracts import StepAudit, StepFailure, StepResult
from app.domain.errors import StepFailed
from app.workflow.routes import (
    DELIVER,
    FINALIZE,
    LOCATE,
    REGRESSION,
    REPAIR,
    REVIEW,
    VALIDATE,
    after_approval,
    after_retry,
)
from app.workflow.steps.request_approval.models import ApprovalInput


def test_after_retry_routes_each_allowed_rejection_target_to_the_named_phase():
    expected = {
        "locate_agent": LOCATE,
        "repair_agent": REPAIR,
        "validate_changes": VALIDATE,
        "run_regression": REGRESSION,
        "review_agent": REVIEW,
        "prepare_delivery": DELIVER,
    }
    for target, phase in expected.items():
        assert after_retry({"retry_target": target, "attempt": 1, "max_attempts": 3}) == phase

    assert after_retry({"retry_target": "untrusted-node", "attempt": 1, "max_attempts": 3}) == REPAIR
    assert after_retry({"retry_target": "repair_agent", "terminal_status": "CANCELLED"}) == FINALIZE


def test_rejection_routes_through_bounded_retry_not_success():
    state = {
        "last_result": "failed",
        "decision": "REJECT",
        "retry_target": "validate_changes",
        "attempt": 1,
        "max_attempts": 3,
    }

    from app.workflow.routes import RETRY

    assert after_approval(state) == RETRY
    assert after_retry(state) == VALIDATE
    # The attempt bound is enforced by the approval router before entering retry_prepare.
    assert after_approval({**state, "attempt": 3}) == FINALIZE


def test_approval_node_preserves_rejection_reason_and_target(monkeypatch):
    import app.workflow.steps.request_approval.node as approval_node

    resume = {
        "decision": "REJECT",
        "approval_fingerprint": "fingerprint",
        "reason": "问题验证不足，需要重跑原始问题验证。",
        "retry_target": "validate_changes",
    }
    monkeypatch.setattr(approval_node, "interrupt", lambda _payload: resume)

    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )
    ctx = SimpleNamespace(services=SimpleNamespace(delivery=None))

    with pytest.raises(StepFailed) as raised:
        approval_node.run(ctx, data)

    assert raised.value.result == "rejected"
    assert raised.value.output["decision"] == "REJECT"
    assert raised.value.output["retry_target"] == "validate_changes"
    assert raised.value.output["retry_reason"] == resume["reason"]


def test_runner_does_not_drop_rejection_retry_target(monkeypatch):
    import app.workflow.runner as runner_module

    result = StepResult(
        status="failed",
        output={
            "decision": "REJECT",
            "passed": False,
            "reason": "回归验证不足",
            "retry_reason": "回归验证不足",
            "retry_target": "run_regression",
        },
        failure=StepFailure(code="rejected", reason="人工打回：回归验证不足", recoverable=False),
        next_action="finalize_execution",
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )
    monkeypatch.setattr(runner_module, "run_stage", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(runner_module, "cancellation_requested", lambda *_args, **_kwargs: False)

    spec = SimpleNamespace(
        name="request_approval",
        task_level=True,
        input_record=None,
        output_record=None,
        next_step="finalize_execution",
        patch=lambda output: {
            "decision": output["decision"],
            "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        },
    )
    store = SimpleNamespace(root=Path("/tmp"))
    node = runner_module.bind(spec, store, services=SimpleNamespace())

    state = node({"run_id": "run-id", "task_id": "task-id"})

    assert state["decision"] == "REJECT"
    assert state["retry_target"] == "run_regression"
    assert state["retry_reason"] == "回归验证不足"
    assert state["last_result"] == "failed"

@pytest.mark.parametrize(
    "retry_target",
    [
        "locate_agent",
        "repair_agent",
        "validate_changes",
        "run_regression",
        "review_agent",
        "prepare_delivery",
    ],
)
def test_approval_node_preserves_every_supported_retry_target(monkeypatch, retry_target):
    import app.workflow.steps.request_approval.node as approval_node

    reason = f"需要从 {retry_target} 重新执行"
    monkeypatch.setattr(
        approval_node,
        "interrupt",
        lambda _payload: {
            "decision": "REJECT",
            "approval_fingerprint": "fingerprint",
            "reason": reason,
            "retry_target": retry_target,
        },
    )
    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )

    with pytest.raises(StepFailed) as raised:
        approval_node.run(SimpleNamespace(services=SimpleNamespace(delivery=None)), data)

    assert raised.value.result == "rejected"
    assert raised.value.output["retry_target"] == retry_target
    assert raised.value.output["retry_reason"] == reason


def test_invalid_retry_target_is_blocked_instead_of_silently_defaulting_to_repair(monkeypatch):
    import app.workflow.steps.request_approval.node as approval_node

    monkeypatch.setattr(
        approval_node,
        "interrupt",
        lambda _payload: {
            "decision": "REJECT",
            "approval_fingerprint": "fingerprint",
            "reason": "invalid recovery target",
            "retry_target": "__import__",
        },
    )
    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )

    with pytest.raises(StepFailed) as raised:
        approval_node.run(SimpleNamespace(services=SimpleNamespace(delivery=None)), data)

    assert raised.value.result == "manual"
    assert raised.value.output["decision"] == "REJECT"
    assert raised.value.output["retry_target"] == ""


def test_runner_blocks_rejected_approval_when_cleanup_or_recovery_is_not_safe(monkeypatch):
    import app.workflow.runner as runner_module

    result = StepResult(
        status="manual",
        output={
            "decision": "REJECT",
            "passed": False,
            "reason": "清理交付分支失败",
            "approval_cleanup_failed": True,
        },
        failure=StepFailure(code="manual", reason="清理交付分支失败", recoverable=False),
        next_action="finalize_execution",
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )
    monkeypatch.setattr(runner_module, "run_stage", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(runner_module, "cancellation_requested", lambda *_args, **_kwargs: False)

    spec = SimpleNamespace(
        name="request_approval",
        task_level=True,
        input_record=None,
        output_record=None,
        next_step="finalize_execution",
        patch=lambda output: {
            "decision": output["decision"],
            "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        },
    )
    node = runner_module.bind(spec, SimpleNamespace(root=Path("/tmp")), services=SimpleNamespace())

    state = node({"run_id": "run-id", "task_id": "task-id"})

    assert state["approval_cleanup_failed"] is True
    assert state["approval_recovery_blocked"] is True
    assert state["retry_target"] == ""
    assert after_approval(state) == FINALIZE


def test_rejection_at_attempt_limit_finalizes_without_another_retry():
    state = {
        "last_result": "failed",
        "decision": "REJECT",
        "retry_target": "repair_agent",
        "attempt": 3,
        "max_attempts": 3,
    }

    assert after_approval(state) == FINALIZE

def test_approval_node_cleanup_failure_is_not_a_retryable_rejection(monkeypatch):
    import app.workflow.steps.request_approval.node as approval_node

    monkeypatch.setattr(
        approval_node,
        "interrupt",
        lambda _payload: {
            "decision": "REJECT",
            "approval_fingerprint": "fingerprint",
            "reason": "代码需要重修",
            "retry_target": "repair_agent",
        },
    )
    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        source_repo_path="/repo",
        repair_branch="repair/carrier",
        delivery_pushed=True,
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )

    with pytest.raises(StepFailed) as raised:
        approval_node.run(SimpleNamespace(services=SimpleNamespace(delivery=None)), data)

    assert raised.value.result == "manual"
    assert raised.value.output["decision"] == "REJECT"
    assert raised.value.output["approval_cleanup_failed"] is True
    assert "retry_target" not in raised.value.output


def test_invalid_rejection_target_is_blocked_by_runner_without_repair_fallback(monkeypatch):
    import app.workflow.runner as runner_module

    result = StepResult(
        status="manual",
        output={
            "decision": "REJECT",
            "passed": False,
            "reason": "驳回回退节点无效",
            "retry_target": "",
        },
        failure=StepFailure(code="manual", reason="驳回回退节点无效", recoverable=False),
        next_action="finalize_execution",
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )
    monkeypatch.setattr(runner_module, "run_stage", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(runner_module, "cancellation_requested", lambda *_args, **_kwargs: False)

    spec = SimpleNamespace(
        name="request_approval",
        task_level=True,
        input_record=None,
        output_record=None,
        next_step="finalize_execution",
        patch=lambda output: {
            "decision": output["decision"],
            "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        },
    )
    node = runner_module.bind(spec, SimpleNamespace(root=Path("/tmp")), services=SimpleNamespace())

    state = node({"run_id": "run-id", "task_id": "task-id"})

    assert state["approval_recovery_blocked"] is True
    assert state["retry_target"] == ""
    assert after_approval(state) == FINALIZE

def test_background_failure_report_persists_redacted_actionable_reason(monkeypatch, tmp_path):
    import workbench.api as api_module

    run_dir = tmp_path / "run-id"
    run_dir.mkdir()
    monkeypatch.setattr(
        api_module,
        "_store",
        lambda: SimpleNamespace(run_dir=lambda _run_id: run_dir),
    )

    api_module._record_background_failure(
        "run-id",
        StepFailed("delivery failed: Authorization: Bearer secret-token-123456"),
        phase="approval_recovery",
    )

    payload = json.loads((run_dir / "reports" / "run_failure.json").read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert "delivery failed" in payload["message"]
    assert "secret-token-123456" not in payload["message"]
    assert payload["error_type"] == "StepFailed"
    assert payload["terminal_step"] == "approval_recovery"
    assert "重新提交决策" in payload["next_action"]


def test_task_detail_api_exposes_run_recovery_failure(monkeypatch, tmp_path):
    import workbench.api as api_module

    run_id = "run-id"
    task_id = "Carrier__framework.track_server__issue"
    run_dir = tmp_path / run_id
    task_dir = run_dir / "tasks" / task_id
    (run_dir / "reports").mkdir(parents=True)
    task_dir.mkdir(parents=True)
    run_failure = {
        "status": "FAILED",
        "error_type": "RuntimeError",
        "message": "后台工作流异常（错误类型：RuntimeError）",
        "terminal_step": "workflow_startup",
        "terminal_reason": "后台工作流异常（错误类型：RuntimeError）",
        "next_action": "检查后台工作流异常后重试。",
    }
    (run_dir / "reports" / "run_failure.json").write_text(
        json.dumps(run_failure, ensure_ascii=False), encoding="utf-8"
    )
    payload = {
        "run_id": run_id,
        "status": "failed",
        "tasks": [{
            "task_id": task_id,
            "carrier": "Carrier",
            "biztype": "framework.track_server",
            "status": "WAITING_APPROVAL",
            "problem": {"error": "original error"},
            "approval_pending": True,
            "steps": [],
            "report": {"result": "WAITING_APPROVAL"},
        }],
    }
    store = SimpleNamespace(
        run_dir=lambda _run_id: run_dir,
        task_dir=lambda _run_id, _task_id: task_dir,
    )
    monkeypatch.setattr(api_module, "_store", lambda: store)
    monkeypatch.setattr(api_module, "_run_payload", lambda _store, _run_id: payload)
    monkeypatch.setattr(api_module, "_v1_run_summary", lambda _payload: {"status": "failed"})

    response = api_module.v1_get_task(run_id, task_id)

    assert response["run_status"] == "failed"
    assert response["run_error"]["status"] == "FAILED"
    assert response["run_error"]["error_type"] == "RuntimeError"
    assert response["task"]["approval_pending"] is True

def test_v1_rejection_api_requires_an_explicit_retry_target():
    from fastapi import HTTPException
    import workbench.api as api_module

    request = api_module.V1ApprovalDecision(
        decision="reject",
        reason="代码修改有误",
    )

    with pytest.raises(HTTPException) as raised:
        api_module.v1_decide_approval("run-id:task-id", request, background=None)

    assert raised.value.status_code == 422
    assert "明确指定回退节点" in raised.value.detail

def test_approval_node_blocks_rejection_without_retry_target(monkeypatch):
    import app.workflow.steps.request_approval.node as approval_node

    monkeypatch.setattr(
        approval_node,
        "interrupt",
        lambda _payload: {
            "decision": "REJECT",
            "approval_fingerprint": "fingerprint",
            "reason": "节点未提供",
            # No retry_target: this must not silently become repair_agent.
        },
    )
    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )

    with pytest.raises(StepFailed) as raised:
        approval_node.run(SimpleNamespace(services=SimpleNamespace(delivery=None)), data)

    assert raised.value.result == "manual"
    assert raised.value.output["decision"] == "REJECT"
    assert raised.value.output["retry_target"] == ""


def test_legacy_rejection_api_requires_an_explicit_retry_target():
    from fastapi import HTTPException
    import workbench.api as api_module

    request = api_module.ApprovalRequest(
        decision="REJECT",
        reason="未指定回退节点",
    )

    with pytest.raises(HTTPException) as raised:
        api_module.decide_approval("run-id", "task-id", request, background=None)

    assert raised.value.status_code == 422
    assert "明确指定回退节点" in raised.value.detail

@pytest.mark.parametrize("retry_target", ["", "__import__", "unknown-node"])
def test_after_approval_never_routes_rejection_to_a_guessed_retry_target(retry_target):
    state = {
        "last_result": "failed",
        "decision": "REJECT",
        "retry_target": retry_target,
        "attempt": 1,
        "max_attempts": 3,
    }

    assert after_approval(state) == FINALIZE

def test_single_step_execution_cannot_bypass_pending_approval_after_run_failure(monkeypatch, tmp_path):
    import app.application.step_runner as step_runner

    records = [
        SimpleNamespace(
            name="request_approval",
            status="waiting",
            output={"decision": "PENDING"},
        )
    ]
    store = SimpleNamespace(
        run_dir=lambda _run_id: tmp_path,
        list_steps=lambda _run_id, _task_id: records,
    )
    spec = SimpleNamespace(
        name="repair_agent",
        task_level=True,
        requires_confirmation=True,
    )
    monkeypatch.setattr(step_runner, "resolve_spec", lambda _name: spec)
    runner = step_runner.StepRunner(store, run_status=lambda _run_id: "failed")

    with pytest.raises(step_runner.StepRunnerError) as raised:
        runner.validate(step_runner.StepRunRequest(
            run_id="run-id",
            task_id="task-id",
            step_name="repair_agent",
            confirm_write=True,
        ))

    assert raised.value.code == "TASK_AWAITING_APPROVAL"

@pytest.mark.parametrize(
    ("retry_target", "expected_phase"),
    [
        ("locate_agent", LOCATE),
        ("repair_agent", REPAIR),
        ("validate_changes", VALIDATE),
        ("run_regression", REGRESSION),
        ("review_agent", REVIEW),
        ("prepare_delivery", DELIVER),
    ],
)
def test_rejected_approval_routes_through_retry_gate_to_the_selected_phase(retry_target, expected_phase):
    state = {
        "last_result": "failed",
        "decision": "REJECT",
        "retry_target": retry_target,
        "attempt": 1,
        "max_attempts": 3,
    }

    from app.workflow.routes import RETRY

    assert after_approval(state) == RETRY
    assert after_retry(state) == expected_phase

