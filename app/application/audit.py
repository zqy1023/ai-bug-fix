"""步骤记录结构与状态映射。

一次执行一份 ``steps.json``。前四个 run 级步骤是顶层 key，任务步骤在 ``tasks`` 下。
``steps.json`` 是一次执行的唯一入口：节点的执行状态、业务结论、摘要、错误和产物
引用都在这里，大体量业务数据落 ``artifacts/`` 按引用读。

``status`` 只表示节点有没有执行成功，``result`` 只表示业务结论，两者不得相互推导。
进程内路由用的 ``last_result``（``app/application/contracts.py`` 的 ``StepStatus``）
不落盘，取值与既有路由行为保持不变。

每一步自己的输入输出模型在各自插件的 ``models.py``；这里只留记录结构、状态映射，
以及按步骤名到插件注册表取模型的入口。
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from app.workflow.spec import FrozenModel, RecordView
from pydantic import BaseModel, Field, model_validator

# 执行状态。原 ``passed`` 改名 ``succeeded``：消歧的是``passed``被执行状态和业务
# 结论共用这件事，不是大小写。
StepPhase = Literal["pending", "running", "succeeded", "failed", "waiting", "skipped"]

# 业务结论。按需增补，不预先铺满：没有消费者之前不添加取值。
StepResultValue = Literal[
    "UNVERIFIED",
    "LOCATED",
    "INSUFFICIENT_EVIDENCE",
    "MANUAL",
    "PASS",
    "FAIL",
    "REJECTED",
    "SKIPPED",
    "DELIVERED",
    "SUCCESS",
    "FAILED",
]

# 产物与报告的固定错误码。与业务结论严格分开，不在各处散字符串。
StepErrorCode = Literal[
    "STEP_EXECUTION_FAILED",
    "ARTIFACT_WRITE_FAILED",
    "ARTIFACT_NOT_FOUND",
    "ARTIFACT_PATH_INVALID",
    "ARTIFACT_INVALID_FORMAT",
    "OUTPUT_REHYDRATION_FAILED",
    "REPORT_WRITE_FAILED",
]

# 落盘裁剪阈值。单条记录要能一眼看完，所以按"关键结论不丢、长内容折叠"来定。
MAX_TEXT_CHARS = 600
MAX_DEPTH = 4
MAX_DEPTH_KEYS = 8
MAX_LIST_ITEMS = 8

# run 级常量：隔离区路径对一次执行是同一个值，每个任务记录各存一遍纯属
# 噪音。它属于 prepare_workspace 的产出，写在 run.json 里一次即可。
RUN_LEVEL_FIELDS = frozenset({"ai_workspace_path"})
_RUN_LEVEL_KEEP = frozenset({"prepare_workspace"})

# 记录格式版本。旧记录没有这个键，按 v0 在读取边界转换，见 RecordStore。
SCHEMA_VERSION = 1


def project(value: Any, view: "RecordView | None", step_name: str = "") -> Any:
    """按步骤声明投影落盘内容。

    只影响 steps.json，不影响运行时数据流：步骤照旧拿到完整数据，只是记录里
    不再重复存放上游已有的副本。投影完再统一剔除 run 级常量。
    """
    result = value
    if view is not None and not view.is_empty() and isinstance(result, dict):
        if view.keep:
            result = {key: item for key, item in result.items() if key in view.keep}
        for dotted, keys in view.trim:
            result = _trim(result, dotted.split("."), keys)
    if step_name not in _RUN_LEVEL_KEEP and isinstance(result, dict):
        result = {key: item for key, item in result.items() if key not in RUN_LEVEL_FIELDS}
    return brief_value(result)


def _trim(value: Any, parts: list[str], keys: tuple[str, ...]) -> Any:
    """按点路径裁字段；路径命中列表时逐项裁。"""
    if not parts or not isinstance(value, (dict, list)):
        return value
    head, rest = parts[0], parts[1:]
    if isinstance(value, list):
        return [_trim(item, parts, keys) for item in value]
    if head not in value:
        return value
    if rest:
        return {**value, head: _trim(value[head], rest, keys)}
    inner = value[head]
    if isinstance(inner, list):
        return {
            **value,
            head: [
                {key: item[key] for key in keys if key in item} if isinstance(item, dict) else item
                for item in inner
            ],
        }
    if isinstance(inner, dict):
        return {**value, head: {key: inner[key] for key in keys if key in inner}}
    return value


def brief_value(value: Any, depth: int = 0) -> Any:
    """把一个输入/输出字段压成适合人读的形态。

    短标量原样保留；长字符串截断并标注原长度；过深的字典折成字段名摘要。目的是让
    ``steps.json`` 里能直接看出这一步的关键字段，而不是塞满上游原文。
    """
    if isinstance(value, str):
        if len(value) <= MAX_TEXT_CHARS:
            return value
        return f"{value[:MAX_TEXT_CHARS]}…(已截断，原长 {len(value)} 字符)"
    if isinstance(value, dict):
        if depth >= MAX_DEPTH and len(value) > MAX_DEPTH_KEYS:
            return {
                "_summary": f"{len(value)} 个字段的深层内容",
                "_keys": sorted(str(key) for key in value)[:MAX_LIST_ITEMS],
            }
        return {str(key): brief_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [brief_value(item, depth + 1) for item in value]
    return value


def brief_record(
    record: "StepRecord",
    input_view: "RecordView | None" = None,
    output_view: "RecordView | None" = None,
) -> dict[str, Any]:
    """导出记录；input/output 各按自己的视图投影后落盘。"""
    return {
        **record.model_dump(mode="json"),
        "input": project(record.input, input_view, record.name),
        "output": project(record.output, output_view, record.name),
    }


# ``summary_of`` 的取值优先级。每个步骤的输出模型都有自己的"一句话"字段，
# 这里按固定顺序取第一个非空字符串，不为 15 个模型各挂一个回调。
SUMMARY_KEYS = ("summary", "reason", "verdict", "status", "delivery_status")


def summary_of(output: dict[str, Any]) -> str:
    """从输出里取一行结论。非字符串或空串一律跳过，全空返回空串。"""
    for key in SUMMARY_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def map_outcome(
    *,
    kind: Literal["ok", "failed", "interrupt"],
    step_result: str | None = None,
    output: dict[str, Any] | None = None,
    output_model: type[BaseModel] | None = None,
    business: str | None = None,
) -> tuple[str, str | None]:
    """进程内结局 → 持久化 ``(status, result)``。这是唯一的映射点。

    ``runner`` 与 ``wiring`` 都调这里，各节点不许自己解释状态。判别事实全部来自
    节点自己的声明，不靠猜：

    - ``kind`` 由 ``runner`` 的 except 通道给出（正常返回 / ``StepFailed`` / 中断）。
    - ``step_result`` 是 ``StepFailed.result`` 原值，``manual`` / ``rejected`` /
      ``skipped`` 优先于通用失败判定。
    - MANUAL/INSUFFICIENT_EVIDENCE 优先保留为人工介入结论。
    - 允许的业务结论从输出模型的 verdict/status/delivery_status 中提取。
    - 业务 FAIL 只认输出模型声明了 ``passed`` 字段且该字段为假；单看 reason
      会把"缺少工作区"这类执行故障误判成业务结论。
    """
    if kind == "interrupt":
        return "waiting", None
    if kind == "ok":
        declared = _business_result(output)
        if declared in {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED"}:
            return "succeeded", declared
        if declared in {"REJECTED", "SKIPPED"}:
            return "succeeded", declared
        if _declared_business_failure(output, output_model):
            return "succeeded", "FAIL"
        return "succeeded", business or declared
    if step_result == "skipped":
        return "skipped", "SKIPPED"
    if step_result == "manual":
        return "succeeded", "MANUAL"
    if step_result == "rejected":
        return "succeeded", "REJECTED"
    if _declared_business_failure(output, output_model):
        return "succeeded", "FAIL"
    return "failed", None


def error_code_of(code: str) -> StepErrorCode:
    """把节点声明的错误码收进固定词汇表；未知码归到通用执行失败。"""
    return code if code in get_args(StepErrorCode) else "STEP_EXECUTION_FAILED"


def _business_result(output: dict[str, Any] | None) -> str | None:
    """只提取契约允许的业务结论；不把普通 status 或 decision 值塞进 result。"""
    if not isinstance(output, dict):
        return None
    allowed = {
        "UNVERIFIED",
        "LOCATED", "INSUFFICIENT_EVIDENCE", "MANUAL", "PASS", "FAIL",
        "REJECTED", "SKIPPED", "DELIVERED", "SUCCESS", "FAILED",
    }
    for key in ("verdict", "delivery_status", "status"):
        value = output.get(key)
        if isinstance(value, str) and value.upper() in allowed:
            return value.upper()
    return None


def _declared_business_failure(
    output: dict[str, Any] | None,
    output_model: type[BaseModel] | None,
) -> bool:
    """该步是否明确声明了业务判定失败。"""
    if output_model is None or output is None:
        return False
    if "passed" not in output_model.model_fields:
        return False
    return output.get("passed") is False


class ArtifactRef(FrozenModel):
    """一份大体量产物的引用。

    ``path`` 是 ``RecordStore.write_artifact`` 返回的、相对于所属根目录的路径。
    ``task_id`` 为空表示 run 级产物：本次 run 内任意任务都可读；非空表示任务级
    产物，只有该任务能读。归属判定不许由调用点自行推断，一律走 ``store.read_ref``。
    """

    # name：该模型字段用于承载对应契约中的结构化数据。
    name: str
    # path：该模型字段用于承载对应契约中的结构化数据。
    path: str
    # task_id：该模型字段用于承载对应契约中的结构化数据。
    task_id: str = ""


class StepError(FrozenModel):
    """步骤执行失败的固定错误结构。只在 ``status == "failed"`` 时存在。"""

    # code：该模型字段用于承载对应契约中的结构化数据。
    code: StepErrorCode
    # message：该模型字段用于承载对应契约中的结构化数据。
    message: str
    # retryable：该模型字段用于承载对应契约中的结构化数据。
    retryable: bool = False


class StepRecord(FrozenModel):
    """一个步骤的完整记录，写入 ``steps.json``。"""

    # name：该模型字段用于承载对应契约中的结构化数据。
    name: str
    # title：该模型字段用于承载对应契约中的结构化数据。
    title: str
    # status：该模型字段用于承载对应契约中的结构化数据。
    status: StepPhase = "pending"
    # result：该模型字段用于承载对应契约中的结构化数据。
    result: StepResultValue | None = None
    # attempt：该模型字段用于承载对应契约中的结构化数据。
    attempt: int = 0
    # started_at：该模型字段用于承载对应契约中的结构化数据。
    started_at: str = ""
    # finished_at：该模型字段用于承载对应契约中的结构化数据。
    finished_at: str = ""
    # duration_ms：该模型字段用于承载对应契约中的结构化数据。
    duration_ms: float = 0.0
    # summary：该模型字段用于承载对应契约中的结构化数据。
    summary: str = ""
    # error：该模型字段用于承载对应契约中的结构化数据。
    error: StepError | None = None
    # artifacts：该模型字段用于承载对应契约中的结构化数据。
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    # input：该模型字段用于承载对应契约中的结构化数据。
    input: dict[str, Any] = Field(default_factory=dict)
    # output：该模型字段用于承载对应契约中的结构化数据。
    output: dict[str, Any] = Field(default_factory=dict)
    # schema_version：该模型字段用于承载对应契约中的结构化数据。
    schema_version: int = SCHEMA_VERSION

    @model_validator(mode="after")
    def _error_matches_status(self) -> "StepRecord":
        """业务结论与执行状态不得相互推导：只有失败的节点才带错误对象。"""
        if self.status != "failed" and self.error is not None:
            raise ValueError(f"状态 {self.status} 的步骤记录不能携带 error")
        return self

    def public(
        self,
        input_view: "RecordView | None" = None,
        output_view: "RecordView | None" = None,
    ) -> dict[str, Any]:
        """导出给报告和接口的固定字段；input/output 按视图投影后落盘。"""
        return brief_record(self, input_view, output_view)


def _model_for(step_name: str, attribute: str) -> type[BaseModel]:
    """按步骤名从插件注册表取输入或输出模型。

    注册表由各步骤自己的 ``SPEC`` 填充，这里不再维护步名到模型的对照表。
    """
    from app.workflow.plugin_loader import load_step
    from app.workflow.registry import PluginError, resolve
    from app.workflow.spec import StepSpec

    load_step(step_name)
    spec = resolve(step_name)
    if not isinstance(spec, StepSpec):
        raise PluginError(f"{step_name} 不是步骤插件，没有 {attribute}")
    return getattr(spec, attribute)


def dump_model(model: BaseModel) -> dict[str, Any]:
    """把步骤模型导出成可写入 ``steps.json`` 的字典。"""
    return model.model_dump()


def parse_input(step_name: str, payload: dict[str, Any]) -> BaseModel:
    """按步骤名校验输入；多余字段直接报错。"""
    return _model_for(step_name, "input_model").model_validate(payload)


def parse_output(step_name: str, payload: dict[str, Any]) -> BaseModel:
    """按步骤名校验输出；多余字段直接报错。"""
    return _model_for(step_name, "output_model").model_validate(payload)


def output_model_of(step_name: str) -> type[BaseModel]:
    """按步骤名取输出模型，供状态映射判断该步是否声明了业务判定字段。"""
    return _model_for(step_name, "output_model")


__all__ = [
    "MAX_DEPTH",
    "MAX_LIST_ITEMS",
    "MAX_TEXT_CHARS",
    "RUN_LEVEL_FIELDS",
    "SCHEMA_VERSION",
    "SUMMARY_KEYS",
    "ArtifactRef",
    "StepError",
    "StepErrorCode",
    "StepPhase",
    "StepRecord",
    "StepResultValue",
    "brief_record",
    "brief_value",
    "dump_model",
    "error_code_of",
    "map_outcome",
    "output_model_of",
    "parse_input",
    "parse_output",
    "project",
    "summary_of",
]
