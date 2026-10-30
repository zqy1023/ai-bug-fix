"""审计负例穿过真实 Agent、图状态、候选门禁和固定命令执行器。"""
from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langgraph.graph import END, START, StateGraph

from agents.review import SPEC
from agents.review.contract import REVIEW_CONTRACT_VERSION
from agents.review.graph import review
from agents.unified_fixer.graph import _ast_check_files, run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from core.contracts import WORKFLOW_VERSION, StepRecord, TaskSnapshot
from core.errors import StepFailed
from infrastructure.storage.store import RecordStore
from infrastructure.workspace.validation import run_validation
from workflow.engine.graph import _retry_patch, build_agent_node
from workflow.engine.handoff import WorkflowState, evidence_version
from workflow.engine.routes import REPORT, REVIEW, RETRY, UNIFIED_FIXER, after_review
from workflow.stages.finalize.models import FinalizeInput
from workflow.stages.finalize.node import build_report
from workflow.stages.generate_report import _input
from workflow.stages.generate_report.models import DeliveryInput
from workflow.stages.generate_report.node import run as deliver
from workflow.stages.prepare_context.prepare_source_snapshot import _patch as snapshot_patch

FILE = "gathers/TEST/main.py"


def test_command_timeout_stops_descendant_process(tmp_path: Path) -> None:
    from infrastructure.workspace.commands import run_fixed_commands

    marker = tmp_path / "orphan-completed.txt"
    child = "import time,pathlib; time.sleep(3); pathlib.Path(" + repr(str(marker)) + ").write_text('orphan')"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('started',flush=True); time.sleep(30)"
    result = run_fixed_commands(tmp_path, [[sys.executable, "-c", parent]], timeout_seconds=1)
    assert not result["passed"]
    assert result["results"][0]["returncode"] is None
    assert "started" in result["results"][0]["stdout"]
    time.sleep(3)
    assert not marker.exists(), "超时后子进程仍然执行了副作用"


def test_runtime_configuration_cache_is_outside_source_and_receipt_is_hashed(tmp_path: Path, monkeypatch) -> None:
    import nacos.client
    from infrastructure.workspace.runtime_environment import initialize_framework_runtime, runtime_configuration_receipt
    from yq_common.log_utils import LogUtils
    from yq_common.prometheus_utils.prometheus_utils import PrometheusUtils

    project = tmp_path / 'project'
    project.mkdir()
    (project / 'server.py').touch()
    monkeypatch.chdir(project)
    monkeypatch.setenv('AI_REPAIR_RUNTIME_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setitem(nacos.client.DEFAULTS, 'SNAPSHOT_BASE', nacos.client.DEFAULTS['SNAPSHOT_BASE'])
    with patch.object(LogUtils, 'initialize'), patch.object(PrometheusUtils, 'initialize'):
        initialize_framework_runtime()
    snapshot = Path(nacos.client.DEFAULTS['SNAPSHOT_BASE'])
    assert not snapshot.is_relative_to(project)
    (snapshot / 'config.json+group+tenant').write_text('private-config', encoding='utf-8')
    receipt = runtime_configuration_receipt()
    assert receipt[0]['path'] == 'config.json+group+tenant'
    assert len(receipt[0]['sha256']) == 64
    assert 'private-config' not in json.dumps(receipt)


def decision() -> dict:
    return {
        "verdict": "PASS", "passed": True, "code_verdict": "PASS",
        "coverage": {"decision": "sufficient", "reason": "source checked",
                     "reviewed_symbols": [FILE], "missing_checks": []},
        "dimensions": {key: {"status": "passed", "reason": "source checked", "evidence": [FILE]}
                       for key in ("syntax", "business", "scope")},
        "summary": "static review", "findings": [], "required_changes": [], "todo": [],
    }


@pytest.fixture
def state(tmp_path: Path) -> dict:
    work, base = tmp_path / "work", tmp_path / "base"
    for root, value in ((work, 2), (base, 1)):
        target = root / FILE
        target.parent.mkdir(parents=True)
        target.write_text(f"value = {value}\n", encoding="utf-8")
    payload = decision()
    payload.update(review_contract_version=REVIEW_CONTRACT_VERSION,
                   evidence_version=evidence_version(str(work), [FILE], str(base)))
    return {
        "run_id": "run", "task_id": "task", "attempt": 1,
        "task": {"carrier": "TEST", "biztype": "track", "service_name": "test", "problem_key": "key"},
        "problem": {"carrier": "TEST", "biztype": "track", "service_name": "test", "response": {"value": 1}},
        "framework": "gathers", "evidence": {"framework": "gathers"},
        "ai_workspace_path": str(work), "baseline_dir": str(base), "changed_files": [FILE],
        "unified_fixer": {"status": "FIXED", "category": "carrier", "recommended_scope": "carrier",
                          "analysis": "ANALYSIS_SENTINEL", "acceptance_criteria": ["ACCEPTANCE_SENTINEL"],
                          "change_plan": {"scope": "carrier", "rationale": "PLAN_SENTINEL", "edits": [{"path": FILE}]}},
        "review": payload, "last_result": "passed", "workflow_version": WORKFLOW_VERSION,
    }


def test_policy_survives_snapshot_graph_and_handoff(state: dict) -> None:
    policy = {"automatic_repair": False, "remote_push": False, "unified_model": "fix-model", "review_model": "review-model"}
    graph = StateGraph(WorkflowState)
    graph.add_node("snapshot", lambda value: snapshot_patch({
        "source_project_path": state["baseline_dir"], "agent_policy": policy,
        "policy_sources": {key: "batch" for key in policy},
    }))
    graph.add_edge(START, "snapshot")
    graph.add_edge("snapshot", END)
    result = graph.compile().invoke(state)
    restored = TaskSnapshot.from_graph(result).to_graph()
    assert restored["agent_policy"] == policy
    assert after_review({**restored, "review": {"verdict": "FAIL"}, "automatic_attempt": 1}) == 'finalize_execution'
    candidate = _input({**state, "agent_policy": policy, "allow_push": True})
    assert not candidate["allow_push"]
    assert candidate["agent_policy"]["remote_push"] is False


@pytest.mark.parametrize("unverified", ["syntax", "scope", "all"])
def test_unknown_or_unverified_code_only_delivers_report(state: dict, unverified: str) -> None:
    manual = deepcopy(state["review"])
    manual.update(verdict="MANUAL", passed=False, delivery_eligible=True)
    for key in manual["dimensions"]:
        if key == unverified or unverified == "all":
            manual["dimensions"][key]["status"] = "unverified"
    if unverified == "all":
        manual["code_verdict"] = "UNKNOWN"
    data = _input({**state, "review": manual})
    assert data["analysis_only"]
    assert not data["delivery_eligible"]


def test_real_agent_node_forwards_repair_criteria_policy_and_validation(state: dict, tmp_path: Path) -> None:
    requests = []
    runtime = SimpleNamespace(run=lambda request: requests.append(request) or {"result": json.dumps(decision())})
    store = RecordStore(tmp_path / "runtime")
    with patch("infrastructure.workspace.validation.nacos_get", return_value="{}"):
        output = build_agent_node(SPEC, store, runtime=runtime)({**state, "agent_policy": {"review_model": "custom-review"}})
    assert output["review"]["verdict"] == "MANUAL"
    assert output["review"]["delivery_eligible"]
    assert requests[0].model == "custom-review"
    evidence = requests[0].prompt_context["evidence"]
    assert "ACCEPTANCE_SENTINEL" in json.dumps(evidence)
    assert "ANALYSIS_SENTINEL" in json.dumps(evidence)
    assert evidence["repair_plan"]["rationale"] == "PLAN_SENTINEL"
    assert evidence["validation"]["replay"]["status"] == "unverified"
    assert store.read_artifact("run", "artifacts/validation.json", "task")["executions"] == []


def test_omitted_source_mutation_invalidates_review_and_candidate(state: dict) -> None:
    hidden = Path(state["ai_workspace_path"]) / "gathers/TEST/helper.py"
    hidden.write_text("def broken(:\n", encoding="utf-8")
    assert evidence_version(state["ai_workspace_path"], [FILE], state["baseline_dir"]) != state["review"]["evidence_version"]
    with pytest.raises(ValueError, match="清单"):
        review({**state, **state["task"]}, SimpleNamespace(run=Mock()))
    with pytest.raises(StepFailed):
        _input(state)
    assert not _ast_check_files(Path(state["ai_workspace_path"]), ["gathers/TEST/missing.py"])[0]


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_self_healing_limits_and_omitted_files_are_enforced(state: dict, limit: int) -> None:
    work = Path(state["ai_workspace_path"])
    helper = work / "gathers/TEST/helper.py"
    calls = []
    def runner(**kwargs: object) -> dict:
        calls.append(kwargs)
        helper.write_text("def broken(:\n" if len(calls) < 3 else "value = 3\n", encoding="utf-8")
        return {"result": json.dumps({"status": "FIXED", "changed_files": [FILE]}), "usage": {"total_tokens": 10}}
    runtime = SimpleNamespace(config=UnifiedFixerConfig(max_self_healing_turns=limit), agent_runner=runner)
    incoming = {**state, **state["task"], "workspace_path": str(work),
                "source_snapshot": {"baseline_dir": state["baseline_dir"]}, "agent_policy": {"unified_model": "fake"}}
    with patch("agents.unified_fixer.provider.invoke_with_audit", side_effect=lambda request, invoke: invoke()):
        result = run_unified_fixer(incoming, runtime)
    assert len(calls) == limit + 1
    assert result["passed"] is (limit == 2)
    assert result["changed_files"] == ["gathers/TEST/helper.py", FILE]
    assert result["self_check"]["self_healing_attempts"] == limit
    assert result["_usage"]["total_tokens"] == 10 * (limit + 1)


def test_default_provider_reuses_session_and_closes_it(state: dict) -> None:
    work = Path(state["ai_workspace_path"])
    session = Mock()
    def invoke(*args: object, **kwargs: object) -> dict:
        target = work / FILE
        target.write_text("def broken(:\n" if session.invoke.call_count == 1 else "value = 5\n", encoding="utf-8")
        return {"result": json.dumps({"status": "FIXED", "changed_files": [FILE]})}
    session.invoke.side_effect = invoke
    with patch("agents.unified_fixer.provider.ClaudeSession", return_value=session) as factory, \
         patch("agents.unified_fixer.provider.invoke_with_audit", side_effect=lambda request, invoke: invoke()):
        result = run_unified_fixer({**state, **state["task"], "workspace_path": str(work),
            "agent_policy": {"unified_model": "fake"}, "source_snapshot": {"baseline_dir": state["baseline_dir"]}}, UnifiedFixerConfig())
    assert result["passed"]
    factory.assert_called_once()
    assert session.invoke.call_count == 2
    session.close.assert_called_once()


VALIDATOR = (
    "import json,sys; from pathlib import Path; "
    "data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); "
    "value=int(Path('gathers/TEST/main.py').read_text().split('=')[1]); "
    "print(json.dumps({'reproduced':value==1,'passed':value==2,"
    "'assertions':[{'name':'expected value','passed':value==2}]}))"
)


def test_controlled_validation_runs_baseline_and_patch_and_does_not_touch_source(state: dict) -> None:
    command = [sys.executable, "-c", VALIDATOR, "{input_path}"]
    with patch("infrastructure.workspace.validation.nacos_get", return_value=json.dumps({"replay": [command], "request": [command]})):
        report = run_validation({**state, "carrier": "TEST"})
    assert report["replay"]["status"] == "passed", report
    assert report["request"]["status"] == "passed", report
    assert [item["phase"] for item in report["executions"]] == ["baseline", "patched", "patched"]
    assert (Path(state["baseline_dir"]) / FILE).read_text() == "value = 1\n"
    assert (Path(state["ai_workspace_path"]) / FILE).read_text() == "value = 2\n"
    assert report["evidence_version"] == state["review"]["evidence_version"]
    requests = []
    with patch("infrastructure.workspace.validation.nacos_get", return_value=json.dumps({"replay": [command], "request": [command]})):
        reviewed = review({**state, **state["task"], "repair": state["unified_fixer"]},
                          SimpleNamespace(run=lambda request: requests.append(request) or {"result": json.dumps(decision())}))
    assert reviewed["verdict"] == "PASS"
    assert len(requests[0].prompt_context["evidence"]["validation"]["executions"]) == 3


@pytest.mark.parametrize("code", ["print('ok')", "import time; time.sleep(3)", "raise RuntimeError('failed')"])
def test_exit_zero_timeout_and_failure_do_not_forge_business_proof(state: dict, code: str) -> None:
    config = {"request": [[sys.executable, "-c", code, "{input_path}"]], "timeout_seconds": 1}
    with patch("infrastructure.workspace.validation.nacos_get", return_value=json.dumps(config)):
        report = run_validation(state)
    assert report["request"]["status"] == "unverified"


def test_report_is_stable_review_snapshot_and_final_version_matches(state: dict, tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "runtime")
    store.save("run", StepRecord(name="review_agent", title="review", status="succeeded", result="PASS", output=state["review"]), "task")
    delivery = Mock()
    delivery.deliver.return_value = {"commit": "abc", "local_branch": True}
    ctx = SimpleNamespace(store=store, run=SimpleNamespace(run_id="run"), task=SimpleNamespace(biztype="track"),
                          services=SimpleNamespace(delivery=delivery))
    data = DeliveryInput.model_validate(_input(state))
    prepared = deliver(ctx, data)
    first = deepcopy(delivery.deliver.call_args.args[0]["repair_report"])
    deliver(ctx, data.model_copy(update={"publish": True, "delivery_candidate_id": prepared.candidate_id}))
    assert delivery.deliver.call_args.args[0]["repair_report"] == first
    assert first["report_kind"] == "repair_review"
    assert first["terminal_step"] == "review_agent"
    assert first["finished_at"]
    assert "WAITING_APPROVAL" not in json.dumps(first)
    assert "resolution" not in first
    assert build_report(ctx, FinalizeInput(task_id="task", carrier="TEST", biztype="track", terminal_status=""))["workflow_version"] == WORKFLOW_VERSION


def test_retry_discards_analysis_only_from_previous_attempt() -> None:
    assert _retry_patch({"analysis_only": True}, UNIFIED_FIXER, {})["analysis_only"] is False


def test_malformed_review_retries_review_instead_of_rewriting_code(state: dict, tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "runtime")
    with patch("infrastructure.workspace.validation.nacos_get", return_value="{}"):
        output = build_agent_node(SPEC, store, runtime=SimpleNamespace(run=lambda request: {"result": "{}"}))(state)
    assert output["review"]["code_verdict"] == "UNKNOWN"
    assert output["review"]["contract_error"].startswith("review_protocol_invalid:")
    assert output["retry_target"] == REVIEW
    assert after_review({**state, **output}) == RETRY
    assert after_review({**state, **output, "validation_recovery_counts": {REVIEW: 2}}) == 'finalize_execution'
    assert store.load("run", SPEC.name, "task").error is None


def test_reviewer_owns_notfound_defect_and_platform_preserves_fail(state: dict) -> None:
    baseline = Path(state["baseline_dir"]) / FILE
    workspace = Path(state["ai_workspace_path"]) / FILE
    baseline.write_text('def parse(events):\n    if not events:\n        raise ValueError("no events")\n', encoding="utf-8")
    workspace.write_text('def parse(events):\n    if not events:\n        return TrackResultModel.not_found()\n', encoding="utf-8")
    response = decision()
    finding = "gathers/TEST/main.py::parse 将空事件的 ValueError 改成无依据的 NotFound，掩盖原异常"
    response.update(verdict="FAIL", passed=False, code_verdict="FAIL", findings=[finding],
                    required_changes=["恢复原错误出口，明确查无结果的业务字段后再新增精确分支"])
    for key in ("business", "scope"):
        response["dimensions"][key].update(status="failed", reason=finding)
    requests = []
    with patch("infrastructure.workspace.validation.nacos_get", return_value="{}"):
        result = review({**state, **state["task"]}, SimpleNamespace(run=lambda request: requests.append(request) or {"result": json.dumps(response)}))
    assert result["verdict"] == "FAIL"
    assert result["code_verdict"] == "FAIL"
    assert not result["delivery_eligible"]
    assert result["findings"] == [finding]
    evidence = requests[0].prompt_context["evidence"]
    assert set(evidence["review_responsibility"]) == {"scope", "business", "regression", "ownership"}
    assert evidence["review_focus"][0]["path"] == FILE
    assert "not_found" not in result["validation"]


def test_platform_does_not_replace_reviewer_business_judgment_with_magic_receipt(state: dict) -> None:
    code = 'def parse(data):\n    if data["error"] == "INVALID_NUMBER":\n        return Result.not_found()\n'
    (Path(state["ai_workspace_path"]) / FILE).write_text(code, encoding="utf-8")
    with patch("infrastructure.workspace.validation.nacos_get", return_value="{}"):
        result = review({**state, **state["task"]}, SimpleNamespace(run=lambda request: {"result": json.dumps(decision())}))
    assert result["code_verdict"] == "PASS"
    assert result["verdict"] == "MANUAL"  # 执行事实未验证，保留代码判断，不由平台猜业务分类。
    assert "not_found" not in result["validation"]
