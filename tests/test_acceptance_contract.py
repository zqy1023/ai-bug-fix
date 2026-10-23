from __future__ import annotations

from copy import deepcopy

import pytest

from app.agents.locate.models import LocateOutput
from app.workflow.validation import (
    acceptance_contract_is_current,
    freeze_acceptance_contract,
)


def _contract() -> dict:
    return {
        "business_goal": "原始时间格式异常得到正确处理并返回有效轨迹",
        "reproduction": ["使用基线源码和原始异常输入复现时间解析失败"],
        "required_assertions": [{"assertion_id": "business_result_1", "expected": "修复后解析结果符合承运商轨迹协议，时间字段可被规范化", "verification_method": "original_response_replay"}],
        "regression_cases": [{"assertion_id": "regression_normal_1", "expected": "已有正常时间格式仍能解析", "verification_method": "request_regression"}, {"assertion_id": "regression_invalid_1", "expected": "无效时间格式仍被明确拒绝", "verification_method": "request_regression"}],
        "verification_methods": ["对原始响应进行受控回放并检查结构化结果"],
        "assumptions": ["原始响应代表该任务的真实失败输入"],
        "known_limitations": [],
    }


def _frozen_state() -> dict:
    state = {
        "problem": {
            "carrier": "Mothership",
            "biztype": "track",
            "service_name": "gather.py.track",
            "error": "时间解析错误",
            "raw_error": "ValueError: invalid time format",
        },
        "baseline_manifest": {"gathers/Mothership/main.py": "baseline-sha256"},
    }
    locate = {
        "status": "LOCATED",
        "root_cause": "原始时间输入未被解析器支持",
        "category": "carrier",
        "source_evidence": [{"path": "gathers/Mothership/main.py", "symbol": "parse_time"}],
        "candidate_files": ["gathers/Mothership/main.py"],
        "recommended_scope": "carrier",
        "acceptance_contract": _contract(),
    }
    state["locate"] = locate
    state.update(freeze_acceptance_contract(state, locate))
    return state


def test_acceptance_contract_is_frozen_against_problem_and_baseline() -> None:
    state = _frozen_state()

    assert acceptance_contract_is_current(state) is True

    changed_problem = deepcopy(state)
    changed_problem["problem"]["error"] = "different source problem"
    assert acceptance_contract_is_current(changed_problem) is False

    changed_baseline = deepcopy(state)
    changed_baseline["baseline_manifest"]["gathers/Mothership/main.py"] = "changed-baseline"
    assert acceptance_contract_is_current(changed_baseline) is False


def test_acceptance_contract_cannot_be_silently_rewritten() -> None:
    state = _frozen_state()
    state["acceptance_contract"]["required_assertions"][0] = "只要日志不再报错就算通过"
    assert acceptance_contract_is_current(state) is False

    changed_root_cause = deepcopy(_frozen_state())
    changed_root_cause["locate"]["root_cause"] = "另一条根因假设"
    assert acceptance_contract_is_current(changed_root_cause) is False


def test_located_result_without_acceptance_contract_is_downgraded_to_manual() -> None:
    result = LocateOutput.model_validate({
        "status": "LOCATED",
        "root_cause": "时间解析分支没有处理原始输入格式",
        "category": "carrier",
        "facts": ["日志记录了时间解析错误"],
        "source_evidence": [{
            "path": "gathers/Mothership/main.py",
            "symbol": "Mothership.parse_time",
            "finding": "该函数对输入时间格式进行解析",
        }],
        "candidate_files": ["gathers/Mothership/main.py"],
        "proposed_edit_files": ["gathers/Mothership/main.py"],
        "evidence_gaps": [],
        "recommended_scope": "carrier",
        "summary": "定位到时间解析分支",
        "passed": True,
    })

    assert result.status == "MANUAL"
    assert result.recommended_scope == "manual"
    assert result.passed is False
    assert any("验收方案" in item for item in result.evidence_gaps)


def test_freezing_requires_original_problem_and_baseline() -> None:
    with pytest.raises(ValueError, match="原始异常"):
        freeze_acceptance_contract(
            {"problem": {}, "baseline_manifest": {"carrier.py": "sha"}},
            {"status": "LOCATED", "root_cause": "根因", "acceptance_contract": _contract()},
        )
