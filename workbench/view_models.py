"""工作台展示契约。操作许可为读取时快照，执行时仍重新验证。"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class ActionAvailability(BaseModel):
    enabled: bool
    reason: str = ""
    requires_confirmation: bool = False


class DeliveryView(BaseModel):
    phase: Literal["none", "prepared", "published", "failed"] = "none"
    candidate_id: str = ""
    commit: str = ""
    branch: str = ""
    pushed: bool = False
    local_branch: bool = False
    binding: dict[str, str] = Field(default_factory=dict)


def delivery_view(steps: list[dict]) -> DeliveryView:
    """只有正式交付成功才标记 published；本地候选始终保留 prepared 身份。"""
    latest = {step.get("name"): step for step in steps}
    candidate = latest.get("prepare_delivery", {})
    published = latest.get("publish_delivery", {})
    step = published or candidate
    output = step.get("output") or {}
    phase = "none"
    if step.get("status") == "failed":
        phase = "failed"
    elif step.get("status") == "succeeded" and output.get("commit"):
        phase = "published" if published else "prepared"
    return DeliveryView(
        phase=phase, candidate_id=str(output.get("candidate_id") or ""),
        commit=str(output.get("commit") or ""), branch=str(output.get("repair_branch") or ""),
        pushed=output.get("pushed") is True, local_branch=output.get("local_branch") is True,
        binding=output.get("binding") or {},
    )


def task_retry_action(*, current: bool, worker_active: bool, run_failed: bool,
                      run_status: str, task: dict) -> ActionAvailability:
    """整任务重试的公共门禁；GET 展示和 POST 执行使用同一判定。"""
    reason = ""
    result = str(task.get("result") or "").upper()
    names = {step.get("name") for step in task.get("steps") or []}
    if not current:
        reason = "历史工作流只读，请创建新执行"
    elif worker_active:
        reason = "后台仍在执行，请等待停止后重试"
    elif run_failed:
        reason = "存在运行级异常，请先重试整个批次"
    elif task.get("approval_pending"):
        reason = "任务正在等待审批，请先完成审批决策"
    elif result in {"MANUAL", "INSUFFICIENT_EVIDENCE", "BLOCKED"}:
        pass
    elif run_status not in {"failed", "partial_failed", "succeeded"}:
        reason = "仅在批次停止后允许重试失败任务"
    elif result not in {"FAILED", "FAIL"}:
        reason = "仅终态失败或需要人工处理的任务支持重试"
    elif "finalize_execution" not in names:
        reason = "任务尚未完成终态收尾"
    elif "prepare_delivery" in names:
        reason = "任务已进入交付阶段，请到失败步骤重试，避免重复提交"
    return ActionAvailability(enabled=not reason, reason=reason)


def run_actions(*, current: bool, status: str, worker_active: bool,
                resumable: bool, safe_startup: bool) -> dict[str, ActionAvailability]:
    allowed = {
        "pause": status in {"running", "queued", "pause_requested"},
        "resume": status == "paused" and resumable and not worker_active,
        "retry": status == "failed" and not worker_active and (resumable or safe_startup),
        "cancel": status in {"running", "queued", "paused", "pause_requested"},
    }
    return {
        name: ActionAvailability(enabled=current and enabled,
                                 reason="" if current and enabled else (
                                     "历史工作流只读，请创建新执行" if not current
                                     else "当前状态或恢复证据不支持此操作"
                                 )) for name, enabled in allowed.items()
    }
