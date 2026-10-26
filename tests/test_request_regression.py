"""真实请求环境、回归结果及工作区副作用边界。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.errors import StepFailed


class ProxyManagerFailFastTests(unittest.TestCase):
    """P1-3：PROXY_MANAGER_ADDRESS 未配置时直接失败且不写 .env。"""

    def test_missing_address_raises_stepfailed_and_writes_nothing(self):
        from app.agents.review.tools.run_regression.environment import (
            prepare_regression_environment,
        )

        clean = {k: v for k, v in os.environ.items() if k != "PROXY_MANAGER_ADDRESS"}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            project.mkdir()
            with patch.dict(os.environ, clean, clear=True):
                with self.assertRaises(StepFailed) as caught:
                    prepare_regression_environment(project)
            self.assertEqual(caught.exception.code, "PROXY_MANAGER_ADDRESS_MISSING")
            self.assertEqual(caught.exception.result, "manual")
            self.assertIn("PROXY_MANAGER_ADDRESS", str(caught.exception))
            self.assertFalse((project / ".env").exists())

    def test_configured_address_writes_env(self):
        from app.agents.review.tools.run_regression.environment import (
            prepare_regression_environment,
        )

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            project.mkdir()
            (project / ".env").write_text("OTHER=1\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {**os.environ, "PROXY_MANAGER_ADDRESS": "http://127.0.0.1:8080"},
            ):
                env = prepare_regression_environment(project)
            self.assertEqual(env["proxy_manager_address"], "http://127.0.0.1:8080")
            content = (project / ".env").read_text(encoding="utf-8")
            self.assertIn('proxy_manager_address="http://127.0.0.1:8080"', content)
            self.assertIn("OTHER=1", content)


class ClassifyRegressionResultTests(unittest.TestCase):
    """S-1：结论分类纯函数逐分支等价（表驱动）。"""

    def _classify(self):
        from app.agents.review.tools.run_regression.node import _classify_regression_result

        return _classify_regression_result

    def _run_case(self, label, result, request_result, supplemental, expected):
        passed, status, reason, failure_kind = self._classify()(
            result, request_result, supplemental
        )
        self.assertEqual(
            (passed, status, reason, failure_kind), expected, msg=label
        )

    def test_table(self):
        fixed_command = [{"command": ["pytest", "tests"], "returncode": 0}]
        py_compile_command = [{"command": ["python", "-m", "py_compile", "a.py"], "returncode": 0}]
        cases = [
            (
                "全通过",
                {"passed": True, "reason": "", "regression": {"results": fixed_command}},
                {},
                {},
                (True, "verified", "固定回归命令执行通过，具体覆盖范围由审查核对", ""),
            ),
            (
                "request failed",
                {"passed": True, "reason": "", "regression": {"results": fixed_command}},
                {"passed": False, "status": "failed", "reason": "真实业务请求未通过"},
                {},
                (False, "verified", "真实业务请求未通过", ""),
            ),
            (
                "unverified",
                {"passed": True, "reason": "", "regression": {"results": fixed_command}},
                {"passed": False, "status": "unverified", "reason": "真实请求超时，未取得完整业务结果"},
                {},
                (False, "unverified", "真实请求超时，未取得完整业务结果", ""),
            ),
            (
                "workspace_changes",
                {
                    "passed": True,
                    "reason": "",
                    "regression": {
                        "results": fixed_command,
                        "workspace_changes": {"status_added": ["?? gathers/Test/new.py"]},
                    },
                },
                {},
                {},
                (False, "unverified", "固定回归命令执行通过，具体覆盖范围由审查核对", "policy"),
            ),
            (
                "execution failure_kind",
                {
                    "passed": False,
                    "failure_kind": "execution",
                    "reason": "回归环境建立失败: clone",
                    "regression": {},
                },
                {},
                {},
                (False, "unverified", "回归环境建立失败: clone", "execution"),
            ),
            (
                "supplemental 不参与结论分类",
                {"passed": True, "reason": "", "regression": {"results": fixed_command}},
                {},
                {"passed": True, "status": "verified", "reason": "补充验证通过"},
                (True, "verified", "固定回归命令执行通过，具体覆盖范围由审查核对", ""),
            ),
            (
                "syntax_only",
                {"passed": True, "reason": "", "regression": {"results": py_compile_command}},
                {},
                {},
                (True, "syntax_only", "仅语法编译通过；未执行行为或集成回归", ""),
            ),
            (
                "not_executed",
                {"passed": True, "reason": "没有需要执行的回归命令", "regression": {"results": []}},
                {},
                {},
                (False, "not_executed", "没有适用于本轮变更的固定命令，未执行补充回归", ""),
            ),
            (
                "runner 失败取执行器原因",
                {"passed": False, "reason": "准生产回归命令执行失败", "regression": {}},
                {},
                {},
                (False, "failed", "准生产回归命令执行失败", ""),
            ),
        ]
        for label, result, request_result, supplemental, expected in cases:
            with self.subTest(case=label):
                self._run_case(label, result, request_result, supplemental, expected)


class RuntimeStatusExemptTests(unittest.TestCase):
    """运行时目录豁免按前缀匹配，源码改动不能被过滤。"""

    def _layout(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        project = workspace / "Src" / "proj"
        project.mkdir(parents=True)
        return workspace, project

    def test_exempt_directory_prefix_match(self):
        from app.agents.review.tools.run_regression.regression import _runtime_status

        workspace, project = self._layout()
        status = "\n".join(
            [
                "!! Src/proj/logs/app.log",
                "!! Src/proj/nacos-data/cache/",
                "?? Src/proj/gathers/Test/new.py",
                " M Src/proj/gathers/Test/main.py",
            ]
        )
        kept, runtime = _runtime_status(status, workspace, project)
        self.assertEqual(
            runtime, ["Src/proj/logs/app.log", "Src/proj/nacos-data/cache/"]
        )
        self.assertNotIn("!! Src/proj/logs/app.log", kept)
        self.assertIn("?? Src/proj/gathers/Test/new.py", kept)
        self.assertIn(" M Src/proj/gathers/Test/main.py", kept)

    def test_non_exempt_new_file_still_reported(self):
        from app.agents.review.tools.run_regression.regression import _runtime_status

        workspace, project = self._layout()
        kept, runtime = _runtime_status("?? Src/proj/other/new.log", workspace, project)
        self.assertEqual(runtime, [])
        self.assertIn("?? Src/proj/other/new.log", kept)

    def test_env_override(self):
        from app.agents.review.tools.run_regression.regression import _runtime_status

        workspace, project = self._layout()
        status = "!! Src/proj/tmp-data/x.log\n!! Src/proj/logs/app.log"
        with patch.dict(
            os.environ, {**os.environ, "REGRESSION_RUNTIME_EXEMPT_DIRS": "tmp-data"}
        ):
            kept, runtime = _runtime_status(status, workspace, project)
        self.assertEqual(runtime, ["Src/proj/tmp-data/x.log"])
        self.assertIn("!! Src/proj/logs/app.log", kept)

    def test_bytecode_cache_is_runtime_artifact(self):
        from app.agents.review.tools.run_regression.regression import _runtime_status

        workspace, project = self._layout()
        status = "\n".join(
            [
                "!! Src/proj/gathers/Test/__pycache__/",
                "?? Src/proj/gathers/Test/parser.cpython-311.pyc",
                "?? Src/proj/gathers/Test/new.py",
            ]
        )
        kept, runtime = _runtime_status(status, workspace, project)
        self.assertEqual(
            runtime,
            [
                "Src/proj/gathers/Test/__pycache__/",
                "Src/proj/gathers/Test/parser.cpython-311.pyc",
            ],
        )
        self.assertNotIn("__pycache__", kept)
        self.assertNotIn(".pyc", kept)
        self.assertIn("?? Src/proj/gathers/Test/new.py", kept)


class BusinessRecoveryTests(unittest.TestCase):
    """审批面业务恢复状态只由真实请求结果决定。"""

    def test_request_status_mapping(self):
        from app.workflow.validation import business_recovery

        self.assertEqual(
            business_recovery({
                "request_regression": {"status": "verified", "passed": True, "reason": "ok"}
            })["status"],
            "verified",
        )
        self.assertEqual(
            business_recovery({
                "request_regression": {"status": "unverified", "passed": False, "reason": "no body"}
            })["status"],
            "unverified",
        )
        self.assertEqual(
            business_recovery({
                "request_regression": {"status": "failed", "passed": False, "reason": "403"}
            })["status"],
            "failed",
        )
        self.assertEqual(business_recovery({})["status"], "not_executed")
        self.assertEqual(business_recovery(None)["status"], "not_executed")


class RequestRunnerTimeoutConstantTests(unittest.TestCase):
    """原始请求未设置超时时，向 track_config 注入有效默认值。"""

    def test_default_timeout_constant_used(self):
        from app.agents.review.tools.run_regression import request_runner

        self.assertEqual(request_runner._DEFAULT_REPLAY_TIMEOUT_MS, 12_000_000)
        script, inputs = request_runner.make_request_script(
            "Test", {"req": {"number": "888001"}}
        )
        self.assertEqual(inputs["track_config"]["timeout"], 12_000_000)
