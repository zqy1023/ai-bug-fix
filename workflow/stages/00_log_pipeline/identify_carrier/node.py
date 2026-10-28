"""03 把分组收成任务引用。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.contracts import StepContext
from core.errors import StepFailed
from core.keys import problem_key, task_id
from core.policy import is_registered_service
from .models import (
    IdentifyCarrierInput,
    IdentifyCarrierOutput,
    TaskRef,
)

# normalize_error 自己写进产物和步骤记录的两套计数，用来对账两个数据源。
_COUNT_KEYS = ("record_count", "ignored_count", "duplicate_count", "unique_count")


def records_of(ctx: StepContext) -> list[dict[str, Any]]:
    """从当前规范化产物读取代表问题，并核对计数。"""
    record = ctx.store.load(ctx.run.run_id, "normalize_error")
    if record is None or record.status != "succeeded":
        raise StepFailed("缺少已通过的 normalize_error")
    refs = [ref for ref in record.artifacts if ref.name == "normalized_records"]
    if not refs:
        raise StepFailed("normalize_error 缺少正式 normalized_records 产物引用")
    payload = ctx.load_artifact(refs[0])
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise StepFailed("normalized_records 产物必须包含 records 数组")
    for key in _COUNT_KEYS:
        if payload.get(key) != record.output.get(key):
            raise StepFailed(
                f"normalize_error 的 {key} 与 normalized_records 产物不一致"
            )
    return payload["records"]


def run(ctx: StepContext, data: IdentifyCarrierInput) -> IdentifyCarrierOutput:
    """保留已注册服务，只为每个业务类型和 carrier 组合创建一个任务。

    同一 ``(biztype, carrier)`` 下的多种异常只保留日志中最早出现的一个
    （``representative_index`` 最小的代表问题），其余异常等下一轮再处理。
    """
    records = records_of(ctx)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    manual = 0
    for record in records:
        if not isinstance(record, dict) or not _ok(record):
            manual += 1
            continue
        groups[(str(record["biztype"]), str(record["carrier"]))].append(record)

    tasks: list[TaskRef] = []
    for (biztype, carrier), problems in sorted(groups.items()):
        record = min(problems, key=lambda item: int(item["representative_index"]))
        source_problem = dict(record["problem"])
        problem = {
            key: source_problem[key]
            for key in ("service_name", "biztype", "carrier", "req", "error")
        }
        # 原始日志原文必须透传：审批与任务详情要用完整 message 判断真实问题，
        # 只剩规范化 error 会丢掉时间戳、堆栈和请求上下文。
        for raw_key in ("raw_error", "message"):
            if source_problem.get(raw_key):
                problem[raw_key] = source_problem[raw_key]
        # 透传可选字段用于 response 重放验证
        if "message" in source_problem:
            problem["message"] = source_problem["message"]
        if "response" in source_problem:
            problem["response"] = source_problem["response"]
        if "track_config" in source_problem:
            problem["track_config"] = source_problem["track_config"]
        fingerprint = problem_key(problem)
        tasks.append(
            TaskRef(
                task_id=task_id(biztype, carrier, fingerprint),
                biztype=biztype,
                carrier=carrier,
                service_name=str(problem["service_name"]),
                problem_key=fingerprint,
                problem=problem,
            )
        )
    output = IdentifyCarrierOutput(tasks=tasks, created=len(tasks), manual=manual)
    return output


def _ok(problem: dict[str, Any]) -> bool:
    """分类字段必须来自日志原值，且服务名已登记。

    旧记录可能缺 ``problem`` 载荷或缺其中后续要用的键；这里补齐 isinstance
    防御，让畸形记录计入 manual，而不是在分组或任务构建时抛 KeyError。
    """
    for field in ("service_name", "biztype", "carrier"):
        value = problem.get(field)
        if not isinstance(value, str) or not value:
            return False
    payload = problem.get("problem")
    if not isinstance(payload, dict):
        return False
    # 只做"键存在"检查：缺键才会让分组/任务构建抛 KeyError；值的类型交给下游契约。
    for field in ("service_name", "biztype", "carrier", "req", "error"):
        if field not in payload:
            return False
    index = problem.get("representative_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return False
    return is_registered_service(str(problem["service_name"]))


__all__ = ["records_of", "run"]

