"""验证环境未就绪不能被当成"业务未通过"（2026-10-31 端到端发现）。

真实缺陷：真实批次 14 个任务里 13 个 ``validation`` 是 ``unverified``，原因是
采集框架在验证副本里没加载起来（``ErrorType.NotImplemented``），不是业务断言
不通过。但平台把两者混成同一个 ``unverified``，于是：

- 审查侧把 business 降级为 ``unverified`` → ``semantic_verified=False``
- 终态判定 ``core/task_outcome.py`` 看到 ``semantic_verified is False`` → FAILED

Brazil 的补丁本身正确（审查 ``code_verdict=PASS``、``coverage=sufficient``、
syntax/scope 全 passed），却被判成 FAILED。

本文件锁住三件事：

1. 验证回执区分「环境未就绪」与「断言未通过」。
2. 环境未就绪时，审查的 business 维度保留模型自己的判断，不强制降级。
3. 环境未就绪的原因与下一步动作能传到最终报告里。
"""

import json
from pathlib import Path

from infrastructure.workspace.validation import _environment_unavailable, _receipt


def _execution(stdout: str) -> dict:
    return {"passed": True, "results": [{"stdout": stdout}]}


def test_receipt_treats_environment_unavailable_as_not_passed():
    receipt = json.dumps({
        "outcome": "environment_unavailable",
        "reason": "采集框架未就绪（NotImplemented）",
        "assertions": [],
        "passed": False,
    })
    assert _receipt(_execution(receipt), baseline=False) is False
    assert _environment_unavailable(_execution(receipt)) is True


def test_environment_unavailable_is_distinct_from_assertion_failure():
    """断言失败不是环境问题：两者必须能被区分开。"""
    failed = json.dumps({
        "outcome": "assertions_failed",
        "reason": "原始请求未返回全部有效轨迹，业务断言未通过",
        "assertions": [{"name": "所有请求均有成功轨迹结果", "passed": False}],
        "passed": False,
    })
    assert _receipt(_execution(failed), baseline=False) is False
    assert _environment_unavailable(_execution(failed)) is False


def test_review_keeps_model_business_judgement_when_environment_blocked(tmp_path):
    """环境未就绪时，审查的 business 维度保留模型判断，不强制降级成 unverified。"""
    from agents.review.graph import review
    from types import SimpleNamespace

    source = tmp_path / "gathers/TEST/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")

    payload = {
        "verdict": "PASS", "passed": True, "code_verdict": "PASS",
        "coverage": {"decision": "sufficient", "reason": "已核对全部改动",
                     "reviewed_symbols": ["gathers/TEST/main.py"], "missing_checks": []},
        "dimensions": {key: {"status": "passed", "reason": "已核对", "evidence": ["x"]}
                       for key in ("syntax", "business", "scope")},
        "summary": "补丁正确", "findings": [], "required_changes": [], "todo": [],
        "unresolved_issues": [],
    }
    state = {
        "run_id": "r", "task_id": "t", "carrier": "TEST", "framework": "gathers",
        "ai_workspace_path": str(tmp_path), "changed_files": ["gathers/TEST/main.py"],
        "attempt": 1,
        "problem": {"carrier": "TEST", "service_name": "test", "biztype": "track"},
    }
    result = review(state, SimpleNamespace(run=lambda request: {"result": json.dumps(payload)}))

    # 复现是审查的一环，不是交付门槛：验证没跑起来时，
    # 代码维度的判断照常生效（verdict 保持 PASS），复现情况单独记录，
    # 业务维度的原因里如实标注"运行验证未执行"，
    # 由交付文档说明，供人工复核时补充。
    assert result["verdict"] == "PASS"
    assert result["dimensions"]["business"]["status"] == "passed", result["dimensions"]["business"]
    assert "运行验证未执行" in result["dimensions"]["business"]["reason"]
    assert result["reproduction"]["state"] == "unavailable", result["reproduction"]
    assert result["reproduction"]["reason"]


def test_validation_runner_marks_not_implemented_as_environment_unavailable():
    """验证器源码必须把 NotImplemented 识别成环境未就绪，而不是断言未通过。"""
    source = Path("infrastructure/workspace/validation_runner.py").read_text(encoding="utf-8")
    assert "environment_unavailable" in source
    assert "NotImplemented" in source


def test_runtime_environment_sanitizes_ssl_keylog():
    """机器级 SSLKEYLOGFILE 指向不可写路径时，导入期就会崩，必须清掉。"""
    import os

    from infrastructure.workspace.runtime_environment import sanitize_ssl_environment

    original = os.environ.get("SSLKEYLOGFILE")
    try:
        os.environ["SSLKEYLOGFILE"] = r"D:\\nss_ssl_sfagent.log"
        removed = sanitize_ssl_environment()
        assert "SSLKEYLOGFILE" in removed
        assert "SSLKEYLOGFILE" not in os.environ
    finally:
        if original is None:
            os.environ.pop("SSLKEYLOGFILE", None)
        else:
            os.environ["SSLKEYLOGFILE"] = original
