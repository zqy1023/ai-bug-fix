from types import SimpleNamespace

from app.agents.common import _prompt_metrics
from app.agents.locate.graph import _locate_contract_summary, _snapshot_prompt_context



def test_locate_default_context_budget_supports_fifty_thousand_characters():
    import json
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[1] / "app" / "agents" / "locate" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["max_context_chars"] == 50000

def test_locate_prompt_snapshot_excludes_full_hash_manifest():
    manifest = {f"gathers/X/module_{index}.py": f"sha-{index}" for index in range(1000)}

    compact = _snapshot_prompt_context(
        {"framework": "gathers", "file_count": 1000, "baseline_manifest": manifest},
        "gathers",
    )

    assert compact == {"framework": "gathers", "file_count": 1000}
    assert "baseline_manifest" not in compact
    assert len(str(compact)) < 100


def test_locate_contract_summary_contains_specific_failure_reason():
    summary = _locate_contract_summary(
        ValueError("candidate file is outside target carrier: gathers/Other/main.py")
    )

    assert "candidate file is outside target carrier" in summary
    assert "gathers/Other/main.py" in summary
    assert "停止自动修复并转人工" in summary


def test_prompt_metrics_explain_sizes_without_persisting_prompt_content():
    request = SimpleNamespace(
        system_prompt="common rules\n\n---\n\nlocate rules",
        prompt="task payload",
        prompt_context={
            "run_id": "run-1",
            "task_id": "task-1",
            "attempt": 1,
            "service_name": "gather.py.track",
            "biztype": "track_server",
            "carrier": "X",
            "framework": "gathers",
            "problem": {"error": "timeout"},
            "source_snapshot": {"framework": "gathers", "file_count": 3},
            "evidence": {"candidates": ["gathers/X/main.py"]},
        },
    )

    metrics = _prompt_metrics(request)

    assert metrics["system_prompt_chars"] == len("common rules\n\n---\n\nlocate rules")
    assert metrics["common_rules_chars"] == len("common rules")
    assert metrics["role_prompt_chars"] == len("locate rules")
    assert metrics["task_context_chars"] == len("task payload")
    assert metrics["problem_chars"] > 0
    assert metrics["source_snapshot_chars"] > 0
    assert metrics["evidence_chars"] > 0
    assert "timeout" not in str(metrics)


def test_locate_provider_detects_disconnect_from_sdk_events_even_when_error_says_success():
    from app.agents.locate.provider import _is_transient_query_error

    error = RuntimeError("Claude 智能体执行失败: success")
    error.agent_events = [
        {"type": "message", "message_type": "AssistantMessage", "message": "API Error: Connection closed mid-response"}
    ]

    assert _is_transient_query_error(error) is True


def test_shared_layer_candidate_is_escalated_to_project_scope_for_change_plan(tmp_path):
    from app.agents.locate.graph import _validate_output_paths
    from app.agents.locate.models import LocateOutput, SourceEvidence

    root = tmp_path / "workspace"
    carrier_root = root / "gathers" / "CarrierA"
    shared_root = root / "common"
    carrier_root.mkdir(parents=True)
    shared_root.mkdir()
    (carrier_root / "main.py").write_text("class CarrierA: pass", encoding="utf-8")
    (shared_root / "redis_utils.py").write_text("def connect(): pass", encoding="utf-8")

    output = LocateOutput(
        status="LOCATED",
        root_cause="共享 Redis 连接层异常",
        category="shared",
        facts=["连接失败发生在共享连接层"],
        source_evidence=[SourceEvidence(path="common/redis_utils.py", symbol="connect", finding="连接异常")],
        candidate_files=["common/redis_utils.py"],
        recommended_scope="carrier",
    )

    _validate_output_paths(root, (str(carrier_root), str(shared_root)), carrier_root, output)

    assert output.recommended_scope == "project"


def test_shared_layer_candidate_outside_allowed_read_roots_is_rejected(tmp_path):
    import pytest
    from app.agents.locate.graph import _validate_output_paths
    from app.agents.locate.models import LocateOutput, SourceEvidence

    root = tmp_path / "workspace"
    carrier_root = root / "gathers" / "CarrierA"
    private_root = root / "secrets"
    carrier_root.mkdir(parents=True)
    private_root.mkdir()
    (carrier_root / "main.py").write_text("class CarrierA: pass", encoding="utf-8")
    (private_root / "credentials.py").write_text("TOKEN = 'not-a-real-secret'", encoding="utf-8")

    output = LocateOutput(
        status="LOCATED",
        root_cause="疑似共享层问题",
        category="shared",
        facts=["候选路径必须经过只读范围校验"],
        source_evidence=[SourceEvidence(path="secrets/credentials.py", symbol="TOKEN", finding="路径测试")],
        candidate_files=["secrets/credentials.py"],
        recommended_scope="project",
    )

    with pytest.raises(ValueError, match="超出只读证据范围"):
        _validate_output_paths(root, (str(carrier_root),), carrier_root, output)

