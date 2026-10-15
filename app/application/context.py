"""执行标识，以及注入单个步骤的依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domain.errors import StepFailed

if TYPE_CHECKING:
    from app.infrastructure.records.store import RecordStore
    from app.application.audit import ArtifactRef
    from app.application.services import WorkflowServices


@dataclass(frozen=True)
class RunContext:
    """一次工作流执行共享的身份与审计信息。"""

    run_id: str
    started_at: str
    source_environment: str | None
    config_version: str | None
    operator: str | None
    audit_id: str | None


@dataclass(frozen=True)
class TaskContext:
    """一个异常任务的身份与产物。"""

    task_id: str
    service_name: str
    biztype: str
    carrier: str
    error_fingerprint: str
    framework: str | None
    source_repo: str
    workspace_path: str | None
    baseline_manifest: dict[str, str] | None
    evidence_refs: tuple[str, ...]
    attempt: int


@dataclass(frozen=True)
class StepContext:
    """一个步骤的依赖、显式的执行/任务身份，以及本次执行的产物收集。

    ``artifacts`` 的作用域是**单次节点执行**：每次 ``run_stage`` 都新建上下文，
    上一次尝试的引用不会混进新记录，重试也不会累积。
    """

    run: RunContext
    task: TaskContext | None
    store: "RecordStore"
    services: "WorkflowServices"
    artifacts: list["ArtifactRef"] = field(default_factory=list)

    def _owner(self) -> str:
        """产物归属：任务级步骤写进任务目录，run 级步骤写进执行目录。"""
        return self.task.task_id if self.task is not None else ""

    def emit_artifact(self, name: str, relative: str, value: Any) -> "ArtifactRef":
        """写一份大产物并返回引用。写失败即该步失败，不留悬空引用。"""
        from app.application.audit import ArtifactRef

        owner = self._owner()
        try:
            path = self.store.write_artifact(self.run.run_id, relative, value, owner)
        except StepFailed:
            raise
        except OSError as error:
            raise StepFailed(
                f"步骤 {name} 写入产物 {relative} 失败: {error}",
                code="ARTIFACT_WRITE_FAILED",
            ) from error
        ref = ArtifactRef(name=name, path=path, task_id=owner)
        self.artifacts.append(ref)
        return ref

    def load_artifact(self, ref: "ArtifactRef") -> Any:
        """按引用读回产物。归属与路径校验统一走 ``store.read_ref``。"""
        return self.store.read_ref(self.run.run_id, ref, self._owner())


__all__ = ["RunContext", "StepContext", "TaskContext"]