"""任务策略：平台决定权限和执行开关，模型只读取冻结后的快照。"""
from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # 保存 provider 返回的真实模型 ID；空字符串表示使用运行时默认模型。
    analysis_model: str = ""
    repair_model: str = ""
    review_model: str = ""
    analyze_legacy: bool = True
    reproduce: bool = True
    acceptance: bool = True
    repair_legacy: bool = False
    repair_shared: bool = False
    shared_paths: list[str] = Field(default_factory=list)
    allow_new_files: bool = False
    allow_code_retirement: bool = False
    verify_fixed: bool = True
    request_regression: bool = True
    automatic_repair: bool = True
    max_repairs: int = Field(default=3, ge=1, le=10)
    local_commit: bool = True
    remote_push: bool = True

    def model_choice(self, role: Literal["locate", "repair", "review"]) -> str:
        return {
            "locate": self.analysis_model,
            "repair": self.repair_model,
            "review": self.review_model,
        }[role]

    @field_validator("shared_paths")
    @classmethod
    def safe_paths(cls, values: list[str]) -> list[str]:
        allowed = {"common", "framework", "gathers_base", "gather_providers"}
        for value in values:
            path = PurePosixPath(value)
            if "\\" in value or path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in allowed:
                raise ValueError("共享写入路径必须位于明确的 common/framework/gathers_base/gather_providers 子目录")
        return sorted(set(values))


def resolve_policy(batch: dict[str, Any] | None = None, task: dict[str, Any] | None = None) -> dict[str, Any]:
    system = json.loads(os.environ.get("AI_REPAIR_AGENT_POLICY", "{}"))
    AgentPolicy.model_validate(system)
    values = {**system, **(batch or {}), **(task or {})}
    return AgentPolicy.model_validate(values).model_dump(mode="json")


def policy_of(state: dict[str, Any]) -> AgentPolicy:
    return AgentPolicy.model_validate(state.get("agent_policy") or (state.get("evidence") or {}).get("agent_policy") or {})


def skipped(reason: str = "用户关闭") -> dict[str, str]:
    return {"status": "skipped", "reason_code": "policy_disabled", "reason": reason}
