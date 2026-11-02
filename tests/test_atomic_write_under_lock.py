"""原子写必须扛住 Windows 文件占用（2026-11-01 端到端发现）。

真实缺陷：工作台正在读 ``final.json`` 时，Windows 上 ``os.replace`` 直接抛
PermissionError（WinError 5），整份 Run 级终态报告写不进去——批次 25 个任务的
结果全丢，只在日志里留下一句"写入 Run 级终态报告失败"。

记录存储（``steps.json``）此前已有重试，但终态报告写入没有共享那份逻辑。
现在两者共用 ``infrastructure.storage.atomic``。
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from infrastructure.storage.atomic import atomic_write_json, atomic_write_text


def test_write_succeeds_when_target_is_free(tmp_path):
    target = tmp_path / "final.json"
    atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_permission_error_is_retried_then_falls_back(tmp_path):
    """目标被占用时先重试；重试仍失败则直接覆盖，不丢内容。"""
    target = tmp_path / "final.json"
    target.write_text("old", encoding="utf-8")
    calls = {"n": 0}
    real_replace = Path.replace

    def flaky(self, other):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "拒绝访问")
        return real_replace(self, other)

    with patch.object(Path, "replace", flaky):
        atomic_write_json(target, {"fresh": True})

    assert calls["n"] == 3, "应重试到成功"
    assert json.loads(target.read_text(encoding="utf-8")) == {"fresh": True}


def test_always_locked_target_still_gets_content(tmp_path):
    """持续占用不截断旧版本，完整新版本保留为可恢复临时文件。"""
    import pytest
    target = tmp_path / "final.json"
    target.write_text("old", encoding="utf-8")

    def locked(self, other):
        raise PermissionError(5, "拒绝访问")

    with patch.object(Path, "replace", locked):
        with pytest.raises(PermissionError):
            atomic_write_json(target, {"fresh": True})

    assert target.read_text(encoding="utf-8") == "old"
    candidates = list(tmp_path.glob("final.json.*.tmp"))
    assert len(candidates) == 1
    assert json.loads(candidates[0].read_text(encoding="utf-8")) == {"fresh": True}


def test_store_and_report_share_the_same_atomic_writer():
    """记录存储与终态报告必须用同一套原子写实现，口径不能分叉。"""
    from infrastructure.storage import store as store_module
    from workflow.engine import graph as graph_module

    assert hasattr(store_module, "atomic_write_text")
    source = Path(graph_module.__file__).read_text(encoding="utf-8")
    assert "atomic_write_json" in source


def test_atomic_write_is_used_by_run_report(tmp_path):
    """终态报告写入必须走原子写（否则文件占用会让整份报告丢失）。"""
    from workflow.engine.graph import _atomic_json

    target = tmp_path / "reports" / "final.json"
    _atomic_json(target, {"result": "SUCCESS"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"result": "SUCCESS"}
