from types import SimpleNamespace

from app.agents.common import _prompt_metrics
from app.agents.locate.graph import _locate_contract_summary, _snapshot_prompt_context


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
        system_prompt="system rules",
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

    assert metrics["system_prompt_chars"] == len("system rules")
    assert metrics["task_context_chars"] == len("task payload")
    assert metrics["problem_chars"] > 0
    assert metrics["source_snapshot_chars"] > 0
    assert metrics["evidence_chars"] > 0
    assert "timeout" not in str(metrics)
