from pathlib import Path

from app.workflow.steps.check_changes.node import _render_diff


def test_render_diff_includes_source_context_around_changed_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "baseline"
    relative = Path("gathers") / "Carrier" / "main.py"
    before_file = baseline / relative
    after_file = workspace / relative
    before_file.parent.mkdir(parents=True)
    after_file.parent.mkdir(parents=True)

    before_file.write_text(
        "def parse():\n"
        "    before = 'left'\n"
        "    target = 'old'\n"
        "    after = 'right'\n"
        "    return target\n",
        encoding="utf-8",
    )
    after_file.write_text(
        "def parse():\n"
        "    before = 'left'\n"
        "    target = 'new'\n"
        "    after = 'right'\n"
        "    return target\n",
        encoding="utf-8",
    )

    diff = _render_diff(
        [{"path": relative.as_posix(), "status": "modified", "in_scope": True, "added": 1, "removed": 1}],
        str(workspace),
        str(baseline),
    )

    assert "@@" in diff
    assert "-    target = 'old'" in diff
    assert "+    target = 'new'" in diff
    assert "    before = 'left'" in diff
    assert "    after = 'right'" in diff
    assert "    return target" in diff


def test_render_diff_never_discloses_out_of_scope_source_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "baseline"
    relative = Path("shared") / "utils.py"
    before_file = baseline / relative
    after_file = workspace / relative
    before_file.parent.mkdir(parents=True)
    after_file.parent.mkdir(parents=True)
    before_file.write_text("TOKEN = 'old-secret-value'\n", encoding="utf-8")
    after_file.write_text("TOKEN = 'new-secret-value'\n", encoding="utf-8")

    diff = _render_diff(
        [{"path": relative.as_posix(), "status": "modified", "in_scope": False, "added": 1, "removed": 1}],
        str(workspace),
        str(baseline),
    )

    assert diff == ""
    assert "old-secret-value" not in diff
    assert "new-secret-value" not in diff
