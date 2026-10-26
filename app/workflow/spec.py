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

    # name：前置步骤的插件名。
    name: str
    # level：该前置属于 run 级还是 task 级记录。
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

    # keep：落盘记录的顶层字段白名单；空元组表示不筛选。
    keep: tuple[str, ...] = ()
    # trim：按点路径裁剪落盘记录，(路径, 该路径保留的字段元组)。
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

    # name：插件名；同时是步骤记录键与图节点名。
    name: str
    # title：工作台展示用的中文标题。
    title: str
    # runner：步骤执行函数 (StepContext, input_model) -> output_model。
    runner: Runner
    # input_model：本步输入的 Pydantic 模型，由 input_builder 的产出校验而来。
    input_model: type[BaseModel]
    # output_model：本步输出的 Pydantic 模型。
    output_model: type[BaseModel]
    # input_builder：从主图状态字典构造本步输入的函数。
    input_builder: InputBuilder
    # task_level：True 表示每个任务各有一条记录，False 表示 run 级单条记录。
    task_level: bool = True
    # requires_confirmation：执行前是否需要人工确认（写类步骤）。
    requires_confirmation: bool = False
    # output_patch：把本步输出回写主图通道的函数；缺省不回写。
    output_patch: OutputPatch | None = None

    # depends_on：单步调试时必须先就绪的前置步骤，取自 definitions 的依赖表。
    @property
    def depends_on(self) -> tuple[Dependency, ...]:
        from app.workflow.definitions import dependencies_for

        return dependencies_for(self.name)

    # input_record：输入在 steps.json 里的落盘投影。
    input_record: RecordView | None = None
    # output_record：输出在 steps.json 里的落盘投影。
    output_record: RecordView | None = None

    def patch(self, output: dict[str, Any]) -> dict[str, Any]:
        """回写字段；没有声明的步骤回写空字典。"""
        return self.output_patch(output) if self.output_patch else {}


@dataclass(frozen=True)
class AgentSpec:
    """一个 agent 子图的自描述。子图本身仍由各自 ``graph.build`` 编译。"""

    # name：插件名；同时是任务图节点名与 agent 记录键。
    name: str
    # title：工作台展示用的中文标题。
    title: str
    # kind：agent 种类（locate/repair/review），用于装配层选择运行时覆盖。
    kind: str
    # input_model：agent 输入的 Pydantic 模型。
    input_model: type[BaseModel]
    # output_model：agent 输出的 Pydantic 模型。
    output_model: type[BaseModel]
    # task_level：当前恒为 True；agent 记录都属于任务级。
    task_level: bool = True
    # requires_confirmation：预留的执行前确认开关。
    requires_confirmation: bool = False

    # depends_on：单步调试时必须先就绪的前置步骤，取自 definitions 的依赖表。
    @property
    def depends_on(self) -> tuple[Dependency, ...]:
        from app.workflow.definitions import dependencies_for

        return dependencies_for(self.name)

    # input_record：输入在 steps.json 里的落盘投影。
    input_record: RecordView | None = None
    # output_record：输出在 steps.json 里的落盘投影。
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
