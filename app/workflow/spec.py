"""插件契约：一个功能点的全部自描述。

步骤、agent、编排组合都只由这里的三张契约描述。装配层读契约，不读具体实现。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.application.context import StepContext

Level = Literal["run", "task"]

Runner = Callable[["StepContext", BaseModel], BaseModel]
InputBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
OutputPatch = Callable[[dict[str, Any]], dict[str, Any]]


class FrozenModel(BaseModel):
    """插件契约基类：未知字段直接拒绝，避免步骤记录再次长成杂物堆。"""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Dependency:
    """单步调试的前置步骤：``run`` 是执行级，``task`` 是任务级。"""

    name: str
    level: Level = "run"


@dataclass(frozen=True)
class RecordView:
    """一步"落盘时留什么"的声明。

    步骤运行时该拿到的数据一点不少，省的只是 ``steps.json`` 里重复存的那几份：
    ES 原文不再全量落盘，代表问题在 ``normalize_error`` 的记录、完整 diff 唯一真身在 ``check_changes``
    的记录里，别处只放引用或统计值。投影只作用于落盘，运行时数据流不动。

    ``keep`` 是顶层白名单，为空表示不筛顶层字段。``trim`` 是点路径裁剪：
    ``("problem", (...))`` 裁字典，``("tasks", (...))`` 裁列表里每一项，
    ``("tasks.problem", (...))`` 则逐层下去裁。
    """

    keep: tuple[str, ...] = ()
    trim: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def is_empty(self) -> bool:
        """没声明任何投影。"""
        return not self.keep and not self.trim


@dataclass(frozen=True)
class StepSpec:
    """一个步骤插件的自描述。

    ``input_builder`` 从主图通道构造本步输入，``output_patch`` 回写后续节点
    需要读取的字段。

    ``input_record`` / ``output_record`` 只影响 steps.json 的落盘内容。
    """

    name: str
    title: str
    runner: Runner
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    input_builder: InputBuilder
    task_level: bool = True
    requires_confirmation: bool = False
    output_patch: OutputPatch | None = None
    depends_on: tuple[Dependency, ...] = ()
    next_step: str = ""
    input_record: RecordView | None = None
    output_record: RecordView | None = None

    def patch(self, output: dict[str, Any]) -> dict[str, Any]:
        """回写字段；没有声明的步骤回写空字典。"""
        return self.output_patch(output) if self.output_patch else {}


@dataclass(frozen=True)
class AgentSpec:
    """一个 agent 子图的自描述。子图本身仍由各自 ``graph.build`` 编译。"""

    name: str
    title: str
    kind: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    task_level: bool = True
    requires_confirmation: bool = False
    depends_on: tuple[Dependency, ...] = ()
    next_step: str = ""
    input_record: RecordView | None = None
    output_record: RecordView | None = None
PluginSpec = StepSpec | AgentSpec

__all__ = [
    "AgentSpec",
    "Dependency",
    "FrozenModel",
    "InputBuilder",
    "Level",
    "OutputPatch",
    "PluginSpec",
    "RecordView",
    "Runner",
    "StepSpec",
]
