"""修补前保存的验收合同：原始问题、基线、策略和必须保留的行为。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.storage.atomic import atomic_write_json


class AcceptanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    problem_id: str
    baseline_id: str
    policy_id: str
    technical_requirements: tuple[str, ...] = ("实际 diff 与声明一致，语法和受控验证独立检查", "执行异常与业务判定分开记录，未知证据不得记作验证通过")
    business_requirements: tuple[str, ...] = ("对照原始异常验证当前 carrier 的实际执行路径", "对照基线核对正常输入、异常输入和空值分支，不改变既有业务语义")
    constraints: tuple[str, ...] = (
        "只修当前授权 carrier 已证明的本地缺陷，不扩大职责或新增 helper",
        "保留原有首项选择、分支优先级、请求身份及正常输出格式",
        "成功、NotFound、错误分类和时间转换必须有当前业务依据",
        "根因与模型建议不改变原始异常、基线及权限约束",
    )
    contract_id: str = Field(default="")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def freeze_acceptance(workspace: Path, problem: dict[str, Any], baseline: str, policy: dict[str, Any]) -> dict[str, Any]:
    from infrastructure.workspace.diff import source_manifest
    manifest = source_manifest(baseline) if baseline else source_manifest(workspace)
    candidate = AcceptanceSpec(problem_id=digest(problem), baseline_id=digest(manifest), policy_id=digest(policy))
    candidate = candidate.model_copy(update={"contract_id": digest(candidate.model_dump(exclude={"contract_id"}))})
    path = (Path(baseline).parent if baseline else workspace) / "artifacts/acceptance.json"
    if path.is_file():
        original = AcceptanceSpec.model_validate_json(path.read_text(encoding="utf-8"))
        if original != candidate:
            raise ValueError("原始验收合同身份不匹配；必须建立新 attempt，不得覆盖旧约束")
        return original.model_dump(mode="json")
    atomic_write_json(path, candidate.model_dump(mode="json"))
    return candidate.model_dump(mode="json")
