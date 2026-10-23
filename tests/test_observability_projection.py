from app.application.observability import project_step


def test_step_projection_omits_bulk_input_and_unrelated_output_by_default() -> None:
    record = {
        "name": "repair_agent",
        "title": "代码修复",
        "status": "succeeded",
        "result": "PASS",
        "summary": "已完成",
        "input": {"workspace": "D:/private/workspace", "prompt": "x" * 10000},
        "output": {
            "summary": "已完成修改",
            "changed_files": ["carrier.py", "parser.py"],
            "full_prompt": "y" * 10000,
            "source_dump": {"content": "z" * 10000},
        },
        "artifacts": [{"name": "diff.patch", "path": "artifacts/diff.patch"}],
    }

    projected = project_step(record)

    assert projected["name"] == "repair_agent"
    assert projected["output"]["summary"] == "已完成修改"
    assert projected["output"]["changed_files"] == ["carrier.py", "parser.py"]
    assert "input" not in projected
    assert "full_prompt" not in projected["output"]
    assert "source_dump" not in projected["output"]
    assert len(str(projected)) < 3000


def test_raw_step_projection_is_explicit_and_still_keeps_step_identity() -> None:
    record = {
        "name": "repair_agent",
        "status": "failed",
        "input": {"prompt": "debug input"},
        "output": {"summary": "debug output", "full": "raw"},
    }

    projected = project_step(record, include_raw=True)

    assert projected["name"] == "repair_agent"
    assert projected["input"] == {"prompt": "debug input"}
    assert projected["output"] == {"summary": "debug output", "full": "raw"}


def test_step_error_is_shortened_for_default_ui_projection() -> None:
    record = {
        "name": "prepare_delivery",
        "status": "failed",
        "error": {"code": "STEP_EXECUTION_FAILED", "message": "x" * 1000, "retryable": True},
        "input": {"analysis_only": True, "report_paths": ["reports/final.json"], "secrets": "not for UI"},
        "output": {"reason": "clone failed", "full_traceback": "y" * 10000},
    }

    projected = project_step(record)

    assert len(projected["error"]["message"]) <= 360
    assert projected["input"] == {"analysis_only": True, "report_paths": ["reports/final.json"]}
    assert "full_traceback" not in projected["output"]
