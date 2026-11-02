"""验证执行与模型分层的回归测试（2026-10-30 第二批修复）。

- 验证副本复用：同一 kind 的多个 phase 只复制一次源码树，且阶段之间互不污染。
- 模型分层：``small_model`` 只在 ``small`` 档生效，未配置时行为不变。
"""

import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from infrastructure.workspace.validation import run_validation

VALIDATOR = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    phase = data["phase"]
    value = int(Path("gathers/TEST/main.py").read_text().split("=")[1])
    # 记录每个阶段看到的版本，用来证明共用副本时 baseline/patched 仍然正确切换
    print(json.dumps({
        "reproduced": value == 1,
        "passed": value == 2,
        "phase_seen": phase,
        "value_seen": value,
        "assertions": [{"name": "phase value", "passed": value == (1 if phase == "baseline" else 2)}],
    }))
    """
)


@pytest.fixture()
def project(tmp_path):
    source, baseline, work = (tmp_path / name for name in ("source", "baseline", "work"))
    for base in (source, baseline, work):
        (base / "gathers/TEST").mkdir(parents=True)
    (source / "gathers/TEST/main.py").write_text("value = 0\n", encoding="utf-8")
    (baseline / "gathers/TEST/main.py").write_text("value = 1\n", encoding="utf-8")
    (work / "gathers/TEST/main.py").write_text("value = 2\n", encoding="utf-8")
    (source / "framework").mkdir()
    (source / "framework/track_server.py").write_text("", encoding="utf-8")
    return {
        "ai_workspace_path": str(work),
        "baseline_dir": str(baseline),
        "source_project_path": str(source),
        "changed_files": ["gathers/TEST/main.py"],
        "carrier": "TEST",
        "problem": {"carrier": "TEST", "response": "recorded",
                    "req": {"carrier": "TEST", "requests": [{"number": "123"}]}},
    }


def test_phases_share_one_copy_but_still_see_their_own_version(project):
    """共用副本后 baseline 看到基线内容，patched 看到补丁内容。"""
    command = [sys.executable, "-c", VALIDATOR, "{input_path}"]
    config = json.dumps({"replay": [command], "request": [command]})
    with patch("infrastructure.workspace.validation.nacos_get", return_value=config):
        report = run_validation(project)

    receipts = [json.loads(item["results"][0]["stdout"].splitlines()[-1])
                for item in report["executions"]]
    # replay: baseline, patched；request: patched
    assert [item["phase_seen"] for item in receipts] == ["baseline", "patched", "patched"]
    assert [item["value_seen"] for item in receipts] == [1, 2, 2]
    assert report["replay"]["status"] == "passed"


def test_source_tree_is_copied_once_per_kind(project):
    """每种验证只复制一次源码树，不再每个 phase 复制一遍。"""
    from infrastructure.workspace import validation as module

    source = Path(project["source_project_path"]).resolve()
    calls = []
    original = module.shutil.copytree

    def counting(*args, **kwargs):
        if Path(args[0]).resolve() == source:
            calls.append(args[0])
        return original(*args, **kwargs)

    command = [sys.executable, "-c", VALIDATOR, "{input_path}"]
    config = json.dumps({"replay": [command], "request": [command]})
    with patch("infrastructure.workspace.validation.nacos_get", return_value=config), \
         patch.object(module.shutil, "copytree", side_effect=counting):
        run_validation(project)

    # replay 一次 + request 一次 = 2 次；旧实现是 baseline/patched/patched 共 3 次。
    assert len(calls) == 2, f"源码树被复制 {len(calls)} 次，应每种验证只复制一次"


def test_runtime_log_from_previous_phase_is_cleared(project):
    """上一阶段写的日志不会让下一阶段丢失 runtime_artifacts 证据。"""
    writer = textwrap.dedent(
        """
        import json, sys
        from pathlib import Path
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        serial = {"baseline": 111, "patched": 222}[data["phase"]]
        log = Path("logs/gather.py.track_2026-10-30_12-00-00_%d.log" % serial)
        log.parent.mkdir(exist_ok=True)
        log.write_text("runtime")
        print(json.dumps({"reproduced": True, "passed": True,
                          "assertions": [{"name": "ok", "passed": True}]}))
        """
    )
    command = [sys.executable, "-c", writer, "{input_path}"]
    config = json.dumps({"replay": [command], "request": [command]})
    with patch("infrastructure.workspace.validation.nacos_get", return_value=config):
        report = run_validation(project)

    # 三次执行都必须识别到自己的新建日志（不是"已存在文件被修改"）。
    assert all(item["runtime_artifacts"] for item in report["executions"]), report["executions"]
    assert all(not item["source_mutations"] for item in report["executions"]), report["executions"]


def test_small_model_only_applies_to_small_tier():
    from core.policy import AgentPolicy

    policy = AgentPolicy(unified_model="big", review_model="rev", small_model="cheap")
    assert policy.model_choice("unified_fixer", "small") == "cheap"
    assert policy.model_choice("unified_fixer", "default") == "big"
    assert policy.model_choice("unified_fixer", "complex") == "big"
    assert policy.model_choice("unified_fixer") == "big"
    assert policy.model_choice("review") == "rev"
    # 未配置 small_model 时不分层，行为与旧实现一致。
    assert AgentPolicy(unified_model="big").model_choice("unified_fixer", "small") == "big"


def test_small_tier_uses_configured_model(tmp_path):
    """small 档确实把 small_model 传进 Agent 请求。"""
    from agents.unified_fixer.graph import run_unified_fixer
    from agents.unified_fixer.models import UnifiedFixerConfig

    carrier = tmp_path / "gathers/TEST"
    carrier.mkdir(parents=True)
    (carrier / "main.py").write_text("value = 1\n", encoding="utf-8")
    seen = []

    def runner(**kwargs):
        seen.append(kwargs)
        (carrier / "main.py").write_text("value = 2\n", encoding="utf-8")
        return {"result": json.dumps({"status": "FIXED",
                                      "changed_files": ["gathers/TEST/main.py"],
                                      "root_cause": "local bug",
                                      "summary": "fixed", "analysis": "local"})}

    state = {
        "run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST",
        "workspace_path": str(tmp_path), "evidence": {"framework": "gathers"},
        "agent_policy": {"unified_model": "big", "small_model": "cheap"},
        "problem": {"carrier": "TEST", "service_name": "test", "biztype": "track",
                    "blame": "code", "frame": "main.py:1:process_html",
                    "response": "recorded"},
    }
    runtime = SimpleNamespace(config=UnifiedFixerConfig(), agent_runner=runner)
    with patch("agents.unified_fixer.provider.invoke_with_audit",
               side_effect=lambda request, invoke: invoke()):
        run_unified_fixer(state, runtime)
    assert seen, "模型应被调用一次"
    assert seen[0]["env"]["ANTHROPIC_MODEL"] == "cheap"
