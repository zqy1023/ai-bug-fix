"""固定修改前验收条件。"""

from typing import Any

from app.domain.errors import StepFailed
from app.workflow.acceptance import AcceptanceInput, define_acceptance
from app.workflow.contracts import AcceptanceContract
from app.workflow.spec import RecordView, StepSpec


def run(ctx, data: AcceptanceInput) -> AcceptanceContract:
    contract = define_acceptance(data)
    ctx.emit_artifact(
        "acceptance_contract",
        "acceptance/contract.json",
        contract.model_dump(mode="json"),
    )
    return contract


def _input(state: dict[str, Any]) -> dict[str, Any]:
    """验收条件只来自确定性快照通道；缺失时结构化失败而不是 KeyError。"""

    def required(key: str) -> Any:
        # `in` 判断与硬下标的 KeyError 条件严格等价；成功路径取值不变。
        if key not in state:
            raise StepFailed(
                f"验收上下文缺失: {key}", code="ACCEPTANCE_CONTEXT_MISSING"
            )
        return state[key]

    return {
        "problem": required("problem"),
        "baseline_dir": required("baseline_dir"),
        "baseline_manifest": required("baseline_manifest"),
        "diagnosis": required("locate"),
    }


SPEC = StepSpec(
    name="define_acceptance",
    title="确定修改前验收条件",
    runner=run,
    input_model=AcceptanceInput,
    output_model=AcceptanceContract,
    input_builder=_input,
    output_patch=lambda output: {"acceptance_contract": output},
    # 落盘投影：baseline_manifest 是"carrier 全部文件 × 哈希"的全量清单，
    # 体积随 carrier 规模线性增长。它不进步骤记录，否则记录会顶破 1 MiB 上限，
    # 而写记录失败会连带让失败记录也写不进去，整轮中止。清单本体已经在
    # source_snapshot.json 产物与状态通道里各有一份。
    input_record=RecordView(
        keep=("problem", "baseline_dir", "diagnosis"),
    ),
)
