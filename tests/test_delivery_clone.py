from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.errors import DeliveryError
from app.infrastructure.git import delivery


class _FakeGit:
    def __init__(self) -> None:
        self.config_calls: list[tuple[str, str]] = []

    def config(self, key: str, value: str) -> None:
        self.config_calls.append((key, value))


class _FakeRepo:
    def __init__(self) -> None:
        self.git = _FakeGit()


def test_clone_retries_without_local_hardlinks_and_uses_git_config_env(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []
    expected = _FakeRepo()

    def fake_clone(url, to_path, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local") is True:
            raise OSError("hardlink unavailable")
        return expected

    monkeypatch.setattr(delivery.Repo, "clone_from", fake_clone)
    result = delivery._clone_repo(tmp_path / "source", tmp_path / "target")

    assert result is expected
    assert len(calls) == 2
    assert calls[0]["local"] is True
    assert "multi_options" not in calls[0]
    assert "multi_options" not in calls[1]
    assert calls[0]["env"]["GIT_CONFIG_KEY_0"] == "core.longpaths"
    assert calls[0]["env"]["GIT_CONFIG_VALUE_0"] == "true"
    assert expected.git.config_calls == [("core.longpaths", "true")]


def test_clone_failure_reports_both_attempts(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def fake_clone(url, to_path, **kwargs):
        nonlocal calls
        calls += 1
        if kwargs.get("local") is True:
            raise OSError("hardlink failed")
        raise RuntimeError("checkout failed")

    monkeypatch.setattr(delivery.Repo, "clone_from", fake_clone)

    with pytest.raises(DeliveryError) as exc:
        delivery._clone_repo(tmp_path / "source", tmp_path / "target")

    assert calls == 2
    assert exc.value.status == "GIT_CLONE_FAILED"
    assert "hardlink failed" in str(exc.value)
    assert "checkout failed" in str(exc.value)



def test_delivery_workspace_preserves_clone_failure_reason(monkeypatch, tmp_path: Path) -> None:
    class _FakeWorkspaces:
        def allocate(self, kind: str, name: str, readable: bool = True) -> Path:
            return tmp_path / "delivery-workspace"

    def fail_clone(git_root: Path, target: Path):
        raise DeliveryError(
            "Git 克隆失败；local=[UnsafeOptionError: invalid clone option]",
            status="GIT_CLONE_FAILED",
        )

    monkeypatch.setattr(delivery, "_clone_repo", fail_clone)
    adapter = delivery.GitBranchDelivery(delivery.GitDeliveryConfig(workspace_manager=_FakeWorkspaces()))

    with pytest.raises(DeliveryError) as exc:
        adapter._delivery_workspace(tmp_path / "source-repo", "fix/example")

    assert exc.value.status == "GIT_CLONE_FAILED"
    assert "UnsafeOptionError" in str(exc.value)
    assert "source-repo" in str(exc.value)
