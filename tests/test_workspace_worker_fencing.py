from pathlib import Path

import pytest

from infrastructure.workspace.workspace import WorkspaceConfig, WorkspaceError, WorkspaceManager


def test_fork_task_workspace_isolates_successor_from_stale_worker(tmp_path):
    manager = WorkspaceManager(WorkspaceConfig(root_dir=tmp_path))
    stale_workspace = tmp_path / "ai" / "run-task-worker-old"
    stale_workspace.mkdir(parents=True)
    (stale_workspace / "carrier.py").write_text("old worker state", encoding="utf-8")

    successor = manager.fork_task_workspace(
        stale_workspace, "run-task-worker-new",
    )
    assert (successor / "carrier.py").read_text(encoding="utf-8") == "old worker state"

    (stale_workspace / "carrier.py").write_text("late stale write", encoding="utf-8")
    assert (successor / "carrier.py").read_text(encoding="utf-8") == "old worker state"

    (successor / "carrier.py").write_text("successor write", encoding="utf-8")
    assert (stale_workspace / "carrier.py").read_text(encoding="utf-8") == "late stale write"


def test_fork_task_workspace_rejects_paths_outside_ai_root(tmp_path):
    manager = WorkspaceManager(WorkspaceConfig(root_dir=tmp_path))
    manager._root = tmp_path
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(WorkspaceError):
        manager.fork_task_workspace(outside, "run-task-worker-new")
