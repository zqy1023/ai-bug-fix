from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from app.infrastructure.filesystem import workspace


def test_git_long_path_config_is_scoped_to_child_process() -> None:
    env = workspace._git_long_path_env()

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.longpaths"
    assert env["GIT_CONFIG_VALUE_0"] == "true"


def test_clone_repo_passes_long_path_config_to_initial_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    fake_repo = Mock()

    with patch.object(workspace.Repo, "clone_from", return_value=fake_repo) as clone:
        result = workspace._clone_repo(source, target, sparse="Src/gather_spider_track")

    assert result is fake_repo
    assert clone.call_count == 1
    kwargs = clone.call_args.kwargs
    assert kwargs["env"]["GIT_CONFIG_KEY_0"] == "core.longpaths"
    assert kwargs["env"]["GIT_CONFIG_VALUE_0"] == "true"
    assert "--config=core.longpaths=true" not in kwargs["multi_options"]
    fake_repo.config_writer.assert_called_once()
    fake_repo.config_writer.return_value.__enter__.return_value.set_value.assert_called_once_with(
        "core", "longpaths", "true"
    )
    # Sparse checkout is invoked after clone; longpaths config must persist in the
    # repository as well as being active for the clone's first checkout.
    fake_repo.git.sparse_checkout.assert_called_once_with(
        "set", "--no-cone", "/Src/gather_spider_track/"
    )
