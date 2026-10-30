import json
from pathlib import Path
from unittest.mock import patch

from infrastructure.llm.session import _mapped_tool_input, _check_tool_access
from infrastructure.llm.tool_budget import ToolBudget
from infrastructure.workspace.diff import complete_changes
from infrastructure.workspace.validation import run_validation
from workflow.engine.handoff import capture_retry_context
from workflow.engine.routes import _analysis_only_task


def test_complete_diff_preserves_bytes_and_added_deleted_empty_binary(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir(); after.mkdir()
    (before / "changed.py").write_bytes(b"old\r\n")
    (after / "changed.py").write_bytes(b"new\r\n")
    (before / "deleted.py").write_text("gone")
    (after / "empty.py").touch()
    (after / "binary.zip").write_bytes(b"PK\x00\xff")
    (after / "final.json").write_text("report")
    files = {item["path"]: item for item in complete_changes(after, before)["files"]}
    assert set(files) == {"changed.py", "deleted.py", "empty.py", "binary.zip"}
    assert files["changed.py"]["before"] == "old\r\n"
    assert "@@ -1 +1 @@" in files["changed.py"]["patch"]
    assert files["deleted.py"]["status"] == "deleted"
    assert files["empty.py"]["status"] == "added"
    assert not files["empty.py"]["before_exists"]
    assert not files["binary.zip"]["available"]


def test_shared_carrier_mistake_maps_only_to_authorized_task_copy(tmp_path):
    work, shared, baseline = (tmp_path / name for name in ("work", "shared", "baseline"))
    for base, name in ((work, "gathers/TEST/main.py"), (shared, "framework/models.py"),
                       (baseline, "gathers/TEST/main.py"), (work, "gathers/OTHER/main.py")):
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text("value = 1")
    roots = [work / "gathers/TEST", shared / "framework", baseline]
    mapped = _mapped_tool_input("Read", {"file_path": str(shared / "gathers/TEST/main.py")}, work, roots)
    assert mapped["file_path"] == str(work / "gathers/TEST/main.py")
    original = {"file_path": str(baseline / "gathers/TEST/main.py")}
    assert _mapped_tool_input("Read", original, work, roots) == original
    wrong = _mapped_tool_input("Read", {"file_path": str(shared / "gathers/OTHER/main.py")}, work, roots)
    assert _check_tool_access("Read", wrong, work, work / "gathers/TEST", {"Read"}, roots)[0] == "deny"
    assert _mapped_tool_input("Edit", original, work, roots) == original
    mapped = _mapped_tool_input("Glob", {"path": str(work), "pattern": "gathers/TEST/**/*.py"}, work, roots)
    assert mapped == {"path": str(work / "gathers/TEST"), "pattern": "**/*.py"}


def test_denials_leave_structured_conclusion_opportunity():
    budget = ToolBudget(2, turn_limit=3)
    for _ in range(3):
        budget.decide("Read", {}, "deny", "scope_denied")
    output = budget.hook_output("Read", {}, "deny", "scope_denied")
    assert "continue_" not in output
    assert budget.stop_kind == "tool_access_blocked"
    assert budget.decide("Read", {"file_path": "another"}, "allow", "")[0] == "deny"
    budget = ToolBudget(2, turn_limit=3)
    budget.turns = 3
    assert budget.decide("Read", {}, "allow", "")[0] == "deny"


def test_exploration_stop_leaves_authorized_patch_room_with_hard_total_limit():
    budget = ToolBudget(4, turn_limit=3, writable=True)
    for index in range(2):
        assert budget.decide("Read", {"file_path": str(index)}, "allow", "")[0] == "allow"
    assert budget.decide("Read", {"file_path": "third"}, "allow", "")[0] == "deny"
    assert budget.stop_kind == "exploration_budget_exhausted"
    for index in range(2):
        assert budget.decide("Edit", {"old_string": str(index), "new_string": "fixed"}, "allow", "")[0] == "allow"
    assert budget.decide("Write", {"content": "extra"}, "allow", "")[0] == "deny"
    assert budget.stop_kind == "tool_budget_exhausted"
    assert budget.usage([])["tool_blocked_writes"] == 1


def test_turn_stop_permits_patch_but_never_bypasses_scope():
    budget = ToolBudget(8, turn_limit=6, writable=True)
    budget.turns = 6
    assert budget.decide("Read", {}, "allow", "")[0] == "deny"
    assert budget.decide("Edit", {"old_string": "old", "new_string": "new"}, "deny", "scope_denied")[0] == "deny"
    assert budget.decide("Edit", {"old_string": "old", "new_string": "new"}, "allow", "")[0] == "allow"
    assert budget.allowed == 1


def test_retry_report_is_not_source_and_delivery_failure_keeps_semantics(tmp_path):
    baseline, work = tmp_path / "baseline", tmp_path / "work"
    baseline.mkdir(); work.mkdir()
    (baseline / "main.py").write_text("one")
    (work / "main.py").write_text("two")
    (work / "final.json").write_text("report")
    context = capture_retry_context({"baseline_dir": str(baseline), "ai_workspace_path": str(work),
                                     "changed_files": ["main.py", "final.json"]})
    assert context["changed_files"] == ["main.py"]
    assert "final.json" not in context["diff"]
    assert _analysis_only_task({"delivery": {"delivery_phase": "FAILED"},
                                "unified_fixer": {"status": "ANALYSIS_ONLY"}})
    assert not _analysis_only_task({"last_result": "FAILED", "unified_fixer": {
        "status": "FIXED", "category": "carrier", "recommended_scope": "carrier"}})


def test_default_validator_executes_baseline_patch_request_in_subprocess(tmp_path):
    source, baseline, work = (tmp_path / name for name in ("source", "baseline", "work"))
    for base in (source, baseline, work):
        (base / "gathers/TEST").mkdir(parents=True)
        (base / "gathers/TEST/main.py").write_text("fixed = " + str(base == work))
    (source / "framework").mkdir()
    (source / "framework/__init__.py").touch()
    (source / "framework/models.py").write_text("class TrackModel:\n    model_validate = staticmethod(lambda value: value)\n")
    (source / "framework/track_server.py").write_text('''from types import SimpleNamespace as NS
from gathers.TEST.main import fixed
class TrackServer:
    async def track(self, model):
        from pathlib import Path
        Path('logs').mkdir(exist_ok=True)
        Path('logs/gather.py.track_2026-10-29_23-00-00_123.log').write_text('runtime log')
        assert model['requests'][0]['number'] == '123'
        assert model['track_config']['timeout'] == 30000
        error = 'Succeed' if fixed else 'ProcessError'
        item = NS(error_type=NS(name=error), request_item=NS(number='123'),
                  track_result=NS(track_result_events=[{'description':'delivered'}]))
        return NS(items=[item], model_dump=lambda **kw: {'error':error, 'replayed':'response' in model})
''')
    state = {"ai_workspace_path": str(work), "baseline_dir": str(baseline),
             "source_project_path": str(source), "changed_files": ["gathers/TEST/main.py"],
             "problem": {"carrier": "TEST", "response": "recorded", "req": {
                 "carrier": "TEST", "track_config": {"timeout": 30000}, "requests": [{"number": "123"}]}}}
    with patch("infrastructure.workspace.validation.nacos_get", return_value="{}"):
        report = run_validation(state)
    assert report["replay"]["status"] == "passed", report
    assert report["request"]["status"] == "passed", report
    receipts = [json.loads(item["results"][0]["stdout"].splitlines()[-1]) for item in report["executions"]]
    assert [item["response"]["replayed"] for item in receipts] == [True, True, False]
    assert not list(source.rglob("__pycache__"))
    assert all(item['runtime_artifacts'] for item in report['executions'])


def test_validation_runtime_logs_do_not_hide_source_mutations():
    from infrastructure.workspace.validation import _validation_changes
    log = 'logs/gather.py.track_2026-10-29_23-00-00_123.log'
    before = {'main.py': 'old', log: 'original-log'}
    new_log = 'logs/gather.py.track_2026-10-29_23-00-01_456.log'
    after = {'main.py': 'changed', log: 'modified-log', new_log: 'new-log', 'logs/hidden.py': 'new-code'}
    changes, artifacts = _validation_changes(before, after)
    assert set(changes) == {'main.py', log, 'logs/hidden.py'}
    assert artifacts == [new_log]


def test_notfound_guard_detects_condition_widening_but_ignores_unrelated_edits(tmp_path):
    from infrastructure.workspace.semantics import not_found_changes
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir(); after.mkdir()
    original = 'def parse(data):\n    if data["error"] == "INVALID_NUMBER":\n        return Result.not_found()\n    return data\n'
    (before / "main.py").write_text(original)
    (after / "main.py").write_text(original.replace('return data', 'return dict(data)'))
    state = {"baseline_dir": str(before), "ai_workspace_path": str(after), "changed_files": ["main.py"]}
    assert not not_found_changes(state)
    (after / "main.py").write_text(original.replace('data["error"] == "INVALID_NUMBER"', 'not data.get("events")'))
    assert not_found_changes(state) == ["main.py"]
