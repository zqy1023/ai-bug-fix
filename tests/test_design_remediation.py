"""设计审查修复的回归测试（2026-10-30）。

覆盖四项 P0/P1 缺陷的修复，每项都锁住"修复后不会再退化"的关键行为：

1. 运行时日志不算源码改动，但改动已有日志仍算；
2. 修复 Agent 的阶段总预算跨会话重建共享；
3. ``repair_shared`` 真正扩大写白名单（此前配置项静默无效）；
4. ``workflow.engine`` 的虚构模块别名已删除，且真实模块可独立导入。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from core.policy import carrier_write_paths


class RuntimeLogIsNotSourceChangeTests(unittest.TestCase):
    """框架自己写的日志不能被判成"未授权源码变化"。"""

    def setUp(self):
        self.service = "gather.py.track"

    def _patch_service(self):
        return patch(
            "infrastructure.workspace.runtime_artifacts.runtime_log_service_name",
            return_value=self.service,
        )

    def test_new_runtime_log_is_not_a_source_change(self):
        from infrastructure.workspace.diff import source_changes

        with patch("infrastructure.workspace.runtime_artifacts.runtime_log_service_name",
                   return_value=self.service):
            baseline, workspace = Path(self._tmp("runtime-log")), None
        # 上面先建结构，避免 patch 作用域把 tmp 目录搞混
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            (workspace / "logs").mkdir(parents=True)
            (baseline / "logs").mkdir(parents=True)
            (workspace / "gathers/TEST").mkdir(parents=True)
            (baseline / "gathers/TEST").mkdir(parents=True)
            (workspace / "gathers/TEST/main.py").write_text("value = 1\n", encoding="utf-8")
            (baseline / "gathers/TEST/main.py").write_text("value = 1\n", encoding="utf-8")
            log = "logs/gather.py.track_2026-10-30_12-00-00_123.log"
            (workspace / log).write_text("frame work init\n", encoding="utf-8")

            with self._patch_service():
                self.assertEqual(source_changes(workspace, baseline), [])

    def test_modified_existing_log_still_counts(self):
        """已有日志被改动说明有人动了文件，必须核验。"""
        import tempfile

        from infrastructure.workspace.diff import source_changes

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            log = "logs/gather.py.track_2026-10-30_12-00-00_123.log"
            for base in (baseline, workspace):
                (base / "logs").mkdir(parents=True)
                (base / log).write_text("before\n", encoding="utf-8")
            (workspace / log).write_text("after\n", encoding="utf-8")

            with self._patch_service():
                self.assertEqual(source_changes(workspace, baseline), [log])

    def test_python_file_under_logs_still_counts(self):
        """``logs/xxx.py`` 是新代码，不是运行时日志。"""
        import tempfile

        from infrastructure.workspace.diff import source_changes

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            for base in (baseline, workspace):
                (base / "logs").mkdir(parents=True)
            (workspace / "logs/hidden.py").write_text("print(1)\n", encoding="utf-8")

            with self._patch_service():
                self.assertEqual(source_changes(workspace, baseline), ["logs/hidden.py"])

    def test_unknown_service_name_does_not_whitelist_anything(self):
        """没有可用服务名时不得放行任何日志。"""
        import tempfile

        from infrastructure.workspace.diff import source_changes

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            log = "logs/anything_2026-10-30_12-00-00_1.log"
            for base in (baseline, workspace):
                (base / "logs").mkdir(parents=True)
            (workspace / log).write_text("x\n", encoding="utf-8")

            with patch("infrastructure.workspace.runtime_artifacts.runtime_log_service_name",
                       return_value=""):
                self.assertEqual(source_changes(workspace, baseline), [log])

    def test_patch_identity_ignores_runtime_logs(self):
        """日志不参与 ``patch_id``，否则审查与交付之间会判"补丁已变化"。"""
        import tempfile

        from workflow.engine.handoff import evidence_version

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir(parents=True)
            (root / "main.py").write_text("value = 1\n", encoding="utf-8")
            with self._patch_service():
                before = evidence_version(str(root), ["main.py"])
                (root / "logs/gather.py.track_2026-10-30_12-00-01_456.log").write_text(
                    "later\n", encoding="utf-8"
                )
                after = evidence_version(str(root), ["main.py"])
            self.assertEqual(before["patch_id"], after["patch_id"])

    def _tmp(self, name):
        return name


class StageDeadlineTests(unittest.TestCase):
    """阶段总预算必须跨会话重建共享，否则一个任务能挂到多倍上限。"""

    def test_deadline_is_not_reset_by_session_rebuild(self):
        import tempfile

        from agents.unified_fixer import provider as module

        config = module.UnifiedFixerConfig(
            timeout_seconds=30, max_provider_attempts=2, max_self_healing_turns=2
        )
        calls = []
        clock = {"now": 1000.0}

        def slow_runner(**kwargs):
            calls.append(kwargs)
            clock["now"] += 20  # 每次调用耗掉 20 秒
            return {"passed": False, "reason": "AGENT_TIMEOUT"}

        class FakeTime:
            @staticmethod
            def monotonic():
                return clock["now"]

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "gathers/TEST").mkdir(parents=True)
            request = _request(workspace)
            with patch.object(module, "time", FakeTime), patch.object(
                module, "invoke_with_audit", side_effect=lambda req, invoke: invoke()
            ), patch.object(module.ToolPolicy, "validate"):
                provider = module.create_provider(config, runner=slow_runner)
                first = provider(request)
                second = provider(request)

        # 预算 30 秒、每次 20 秒：第一轮 30 秒额度，第二轮只剩 10 秒，
        # 第三轮发起前预算已耗尽，必须直接判超时而不是再要一次 30 秒。
        self.assertEqual(len(calls), 2, "超出阶段总预算后不得再发起模型调用")
        self.assertEqual(calls[0]["timeout_seconds"], 30)
        self.assertEqual(calls[1]["timeout_seconds"], 10, "第二轮只能用剩余时间")
        self.assertEqual(first.get("reason"), "AGENT_TIMEOUT")
        self.assertEqual(second.get("reason"), "AGENT_TIMEOUT")


def _request(workspace: Path | None = None):
    from core.contracts import AgentRequest

    root = workspace or Path.cwd()
    return AgentRequest(
        agent_role="unified_fixer",
        run_id="run",
        task_id="task",
        attempt=1,
        prompt_context={"carrier": "TEST", "framework": "gathers",
                        "problem": {"service_name": "s", "biztype": "b", "carrier": "TEST"},
                        "agent_policy": {}},
        input_evidence={},
        budget={"max_turns": 4, "max_tool_calls": 4},
        stage="unified_fixer",
        workspace=str(root),
        system_prompt="sys",
        prompt="do it",
        prompt_version="v",
        prompt_hash="h",
        model="test-model",
        allowed_read_roots=(),
        allowed_write_roots=(),
    )


class RepairSharedPolicyTests(unittest.TestCase):
    """``repair_shared`` 必须是真开关，不能只写在策略里。"""

    def test_shared_paths_only_apply_when_enabled(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gathers/TEST").mkdir(parents=True)
            (root / "framework").mkdir()
            policy = {"repair_shared": True, "shared_paths": ["framework"]}

            enabled = carrier_write_paths(root, "TEST", policy)
            self.assertIn((root / "framework").resolve(), [p.resolve() for p in enabled])

            disabled = carrier_write_paths(root, "TEST", {**policy, "repair_shared": False})
            self.assertNotIn((root / "framework").resolve(), [p.resolve() for p in disabled])

            no_policy = carrier_write_paths(root, "TEST", None)
            self.assertEqual(len(no_policy), 1, "没有策略时只允许 carrier 目录")

    def test_escalating_shared_path_is_rejected_by_validator(self):
        """``shared_paths`` 只允许明确子目录，不能借它写到仓库根。"""
        from pydantic import ValidationError

        from core.policy import AgentPolicy

        for bad in ("../outside", "/abs/path", "gathers\\TEST", "docs"):
            with self.assertRaises(ValidationError, msg=bad):
                AgentPolicy(repair_shared=True, shared_paths=[bad])


class EngineModuleAliasTests(unittest.TestCase):
    """虚构模块别名已删除，静态分析能看见真实依赖。"""
    GHOSTS = (
        "workflow.engine.definitions",
        "workflow.engine.confirmation",
        "workflow.engine.wiring",
        "workflow.engine.entrypoint",
        "workflow.engine.step_runner",
        "workflow.engine.loader",
        "workflow.engine.registry",
        "workflow.engine.retry_context",
        "workflow.engine.evidence_version",
        "workflow.engine.frontend_contracts",
    )

    def test_ghost_modules_are_not_installed(self):
        import workflow.engine  # noqa: F401

        for name in self.GHOSTS:
            self.assertNotIn(name, sys.modules, f"{name} 不应再通过别名存在")

    def test_core_layer_does_not_import_workflow(self):
        """核心层的依赖拓扑不能再反向依赖编排层。"""
        from core import contracts
        from core.dependencies import dependencies_for

        # 属性实际解析到 core.dependencies（同一函数对象），不是 orchestrator。
        self.assertEqual(
            contracts.StepSpec.depends_on.fget.__module__, "core.contracts"
        )
        self.assertEqual(dependencies_for("review_agent")[0].name, "unified_fixer_agent")
        source = Path(contracts.__file__).read_text(encoding="utf-8")
        self.assertNotIn("workflow.engine", source)

    def test_dependency_topology_is_available_without_engine(self):
        from core.dependencies import STEP_DEPENDENCIES, dependencies_for

        self.assertIn("review_agent", STEP_DEPENDENCIES)
        names = [item.name for item in dependencies_for("review_agent")]
        self.assertEqual(names, ["unified_fixer_agent"])


class ConventionFactsTests(unittest.TestCase):
    """用户修复习惯必须是可核对的事实，不只是提示词里的一句话。"""

    def test_new_helper_is_reported_so_review_can_ask_why(self):
        """新增 helper 要能被审查看到，而不是靠模型自觉。"""
        import tempfile

        from core.conventions import convention_facts

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            name = "gathers/TEST/main.py"
            for base in (baseline, workspace):
                (base / "gathers/TEST").mkdir(parents=True)
                (base / name).write_text(
                    "def process_html(self, model):\n    return []\n", encoding="utf-8"
                )
            # 模型新增了一个 helper —— 正是用户明确禁止的做法。
            (workspace / name).write_text(
                "def _check_blocked(text):\n    return 'waf' in text\n\n"
                "def process_html(self, model):\n    if _check_blocked('x'):\n        return None\n",
                encoding="utf-8",
            )
            facts = convention_facts(baseline, workspace, [name], {"carrier_dir": "gathers/TEST"})
        self.assertEqual(facts["new_functions"], [f"{name}::_check_blocked"])
        self.assertTrue(facts["router_readable"])
        self.assertTrue(any("新增了 1 个函数" in item for item in facts["expectations"]))

    def test_single_file_local_edit_has_no_helper_warning(self):
        import tempfile

        from core.conventions import convention_facts

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, workspace = root / "baseline", root / "workspace"
            name = "gathers/TEST/main.py"
            body = "def process_html(self, model):\n    return []\n"
            for base in (baseline, workspace):
                (base / "gathers/TEST").mkdir(parents=True)
            (baseline / name).write_text(body, encoding="utf-8")
            (workspace / name).write_text(body.replace("return []", "return [1]"), encoding="utf-8")
            facts = convention_facts(baseline, workspace, [name], {"carrier_dir": "gathers/TEST"})
        self.assertEqual(facts["new_functions"], [])
        self.assertEqual(facts["changed_file_count"], 1)
        self.assertFalse(any("新增了" in item for item in facts["expectations"]))

    def test_multi_file_edit_raises_a_necessity_question(self):
        import tempfile

        from core.conventions import convention_facts

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "gathers/TEST").mkdir(parents=True)
            for name in ("gathers/TEST/main.py", "gathers/TEST/helper.py"):
                (workspace / name).write_text("value = 1\n", encoding="utf-8")
            facts = convention_facts(None, workspace, ["gathers/TEST/main.py", "gathers/TEST/helper.py"],
                                     {"carrier_dir": "gathers/TEST"})
        self.assertEqual(facts["changed_file_count"], 2)
        self.assertTrue(any("共修改 2 个文件" in item for item in facts["expectations"]))


class DispositionSingleSourceTests(unittest.TestCase):
    """跳过处置只有一个判定入口，不再散在四处。"""

    def test_model_skip_needs_independent_approval(self):
        from core.disposition import skip_approved_by_review

        repair = {"status": "ANALYSIS_ONLY", "attribution_type": "official",
                  "skip_reason": "upstream down"}
        self.assertFalse(skip_approved_by_review(repair, {}))
        self.assertFalse(skip_approved_by_review(repair, {"verdict": "MANUAL", "passed": False}))

    def test_deterministic_skip_is_not_a_model_proposal(self):
        from core.disposition import deterministic_skip

        # 平台策略跳过：没有模型 skip_reason，归因也不是 official/environment。
        self.assertTrue(deterministic_skip({"skip_reason": "策略关闭旧框架分析"}, {}))
        # 模型自己说不用改 → 不算平台确定性跳过。
        self.assertFalse(deterministic_skip({}, {"status": "ANALYSIS_ONLY",
                                                 "attribution_type": "official",
                                                 "skip_reason": "upstream down"}))

    def test_unresolved_blocks_patch_not_analysis(self):
        from core.disposition import unresolved_blocks_delivery

        self.assertTrue(unresolved_blocks_delivery(
            {"review_kind": "patch", "unresolved_issues": ["x"]}))
        self.assertFalse(unresolved_blocks_delivery(
            {"review_kind": "analysis_disposition", "unresolved_issues": ["x"]}))


if __name__ == "__main__":
    unittest.main()
