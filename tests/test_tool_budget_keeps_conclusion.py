"""工具预算耗尽不能抹掉已经产出的分析结论（2026-10-31 端到端发现）。

真实缺陷：DHLink 那轮模型输出了**完整正确**的归因（"旧插件把 logisticNo 写成
结果 number，导致旧框架桥接按请求单号匹配失败"），只因为工具放行预算耗尽，
平台就把它标成 ``failure_kind=tool_budget_exhausted`` + ``result=failed``，
任务终态 FAILED——结论本身没有任何问题。

正确语义：

- **写入被拒绝**（tool_blocked_writes）：修复确实没完成 → 失败。
- **工具被阻断且没有可用结论** → 失败（没有结论就没有交付物）。
- **工具被阻断但已有结构化结论** → 保留结论，把阻断记成证据缺口，
  交独立审查判断。
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig


def _run(tmp_path, payload, usage_extra=None):
    carrier = tmp_path / "gathers/TEST"
    carrier.mkdir(parents=True)
    (carrier / "main.py").write_text("value = 1\n", encoding="utf-8")
    usage = {"tool_calls": 16, "tool_stop_kind": "tool_budget_exhausted",
             "tool_stop_reason": "工具放行预算 16 已耗尽；停止工具调用并输出当前结论与缺口"}
    usage.update(usage_extra or {})

    def provider(request):
        return {"result": json.dumps(payload), "usage": usage}

    state = {
        "run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST",
        "workspace_path": str(tmp_path), "evidence": {"framework": "gathers"},
        "agent_policy": {"unified_model": "test-model"},
        "problem": {"carrier": "TEST", "service_name": "test", "biztype": "track"},
    }
    runtime = SimpleNamespace(config=UnifiedFixerConfig(), agent_runner=provider)
    with patch("agents.unified_fixer.graph.create_provider", return_value=Mock(side_effect=provider)):
        return run_unified_fixer(state, runtime)


def test_budget_exhausted_keeps_existing_analysis(tmp_path):
    """已有完整结论时，预算耗尽不能把它变成失败。"""
    payload = {
        "status": "ANALYSIS_ONLY",
        "attribution_type": "code",
        "root_cause": "旧插件把 logisticNo 写成结果 number，桥接按请求单号匹配失败",
        "summary": "本地字段映射缺陷，不是网络或上游故障",
        "analysis": "沿调用链核对到字段赋值处",
        "evidence_gaps": [],
        "changed_files": [],
        "passed": False,
        "result": "passed",
    }
    result = _run(tmp_path, payload)
    assert not result.get("failure_kind"), result.get("failure_kind")
    assert result["result"] == "passed", result["result"]
    assert "root_cause" in result
    assert any("工具调用被平台阻断" in item for item in result.get("evidence_gaps") or []), result.get("evidence_gaps")


def test_blocked_write_without_change_is_still_failure(tmp_path):
    """写入被拒绝：修复确实没完成，必须判失败。"""
    payload = {
        "status": "FIXED",
        "attribution_type": "code",
        "root_cause": "本地缺陷",
        "summary": "准备修复",
        "changed_files": [],
        "passed": True,
        "result": "passed",
    }
    result = _run(tmp_path, payload, {"tool_blocked_writes": 1})
    assert result.get("failure_kind") == "tool_budget_exhausted"
    assert result["result"] == "failed"


def test_budget_exhausted_without_conclusion_is_failure(tmp_path):
    """阻断发生且没有任何结论 → 没有交付物，按失败处理。"""
    payload = {"status": "MANUAL", "changed_files": [], "passed": False, "result": "failed"}
    result = _run(tmp_path, payload)
    assert result.get("failure_kind") == "tool_budget_exhausted"
    assert result["result"] == "failed"
