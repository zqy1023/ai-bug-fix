from __future__ import annotations

import pytest

from app.domain.errors import StepFailed
from app.workflow.steps.apply_plan.node import _resolve


def test_resolve_accepts_workspace_relative_file(tmp_path) -> None:
    target = _resolve(tmp_path, "gathers/DHL/main.py")
    assert target == (tmp_path / "gathers" / "DHL" / "main.py").resolve()


@pytest.mark.parametrize("relative", ["../outside.py", "/tmp/outside.py", ""])
def test_resolve_rejects_paths_outside_workspace(tmp_path, relative: str) -> None:
    with pytest.raises(StepFailed):
        _resolve(tmp_path, relative)
