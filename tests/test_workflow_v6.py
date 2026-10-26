"""工作流契约、失效规则与完整图路径的离线回归。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.workflow.acceptance import (
    AcceptanceInput,
    define_acceptance,
    validate_acceptance,
)
from app.workflow.contracts import Assessment, ExecutionResult, Handoff, StepOutcome
from app.workflow.definitions import TASK_NODE_NAMES, dependencies_for
from app.workflow.graph import build_task_graph
from app.workflow.handoff import apply_step_result, outcome_of
from app.workflow.validation import decide, make_plan, summarize


class MemoryStore:
    def __init__(self):
        self.values = {}

    def write_artifact(self, run_id, name, value, task_id=""):
        self.values[run_id, task_id, name] = value
        return name


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.baseline = self.root / "baseline"
        self.workspace = self.root / "workspace"
        relative = "gathers/Test/main.py"
        for root, content in (
            (self.baseline, "def parse():\n    raise ValueError('bad')\n"),
            (self.workspace, "def parse():\n    return 1\n"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(content, encoding="utf-8")
        manifest = {
            relative: hashlib.sha256(
                (self.baseline / relative).read_bytes()
            ).hexdigest()
        }
        self.state = {
            "run_id": "run",
            "task_id": "task",
            "workflow_version": 7,
            "problem": {
                "service_name": "test",
                "biztype": "tracking",
                "carrier": "Test",
                "error": "ValueError: bad",
                "response": "recorded",
                "req": "{}",
            },
            "locate": {
                "status": "LOCATED",
                "category": "carrier",
                "recommended_scope": "carrier",
                "passed": True,
            },
            "baseline_dir": str(self.baseline),
            "baseline_manifest": manifest,
            "ai_workspace_path": str(self.workspace),
            "changed_files": [relative],
            "gate_result": {"passed": True},
            "syntax_result": {"passed": True},
            "review_mode": "human",
            "max_attempts": 3,
        }
        contract = define_acceptance(
            AcceptanceInput(
                problem=self.state["problem"],
                baseline_dir=str(self.baseline),
                baseline_manifest=manifest,
                diagnosis=self.state["locate"],
            )
        )
        self.state["acceptance_contract"] = contract.model_dump(mode="json")
        self.state["baseline_reproduction"] = {
            "status": "passed",
            "problem_id": contract.problem_id,
            "baseline_id": contract.baseline_id,
        }
        plan = make_plan(self.state)
        self.state["validation_plan"] = plan
        self.state["gate_result"] = {
            "passed": True,
            "evidence_version": {
                **plan["evidence_version"],
                "policy_id": plan["gate_policy_id"],
            },
        }
        self.state["verify_result"] = self.issue_report()
        self.state["regression_result"] = {
            "passed": True,
            "status": "verified",
            "input_id": plan["input_id"],
            "evidence_version": plan["evidence_version"],
            "request_regression": {
                "passed": True,
                "status": "verified",
                "entrypoint": "server_dev.main",
            },
        }

    def tearDown(self):
        self.directory.cleanup()

    def issue_report(self):
        plan = self.state["validation_plan"]
        return {
            "passed": True,
            "status": "verified",
            "mode": "response_replay",
            "input_id": plan["input_id"],
            "evidence_version": plan["evidence_version"],
            "verification": {
                "schema_version": 5,
                "scope": "parser_only",
                "assertions": [
                    {"key": key, "status": "passed"}
                    for key in ("input", "baseline", "fixed")
                ],
            },
        }

    def review_report(self):
        from app.agents.review.contract import REVIEW_CONTRACT_VERSION
        return {
            "verdict": "PASS", "passed": True, "code_verdict": "PASS",
            "review_contract_version": REVIEW_CONTRACT_VERSION,
            "evidence_version": self.state["validation_plan"]["evidence_version"],
            "coverage": {"decision": "sufficient", "reason": "已核对实际改动分支",
                         "reviewed_symbols": ["parse"], "missing_checks": []},
            "dimensions": {key: {"status": "passed", "reason": "已核对执行证据与源码",
                                 "evidence": ["gathers/Test/main.py:parse"]}
                           for key in ("syntax", "business", "scope")},
            "summary": "代码已修复原异常", "findings": [], "required_changes": [],
            "environment_assessment": "网络受限", "upstream_assessment": "无法确认",
        }

    def test_real_review_projects_three_dimensions_and_environment(self):
        from app.agents.review.graph import review
        runtime = SimpleNamespace(run=lambda request: self.review_report(), env={"ANTHROPIC_MODEL": "test-model"})
        result = review({**self.state, "carrier": "Test", "framework": "gathers",
                         "check_passed": True, "syntax_passed": True, "attempt": 1,
                         "problem": {**self.state["problem"], "service_name": "test", "biztype": "tracking", "carrier": "Test"}}, runtime)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual([row["key"] for row in result["checks"]], ["syntax", "business", "scope"])
        self.assertEqual(result["environment_assessment"], "网络受限")
        self.assertEqual(result["upstream_assessment"], "无法确认")

    def test_manual_provider_returns_valid_pending_three_dimension_review(self):
        from app.agents.review.config import ReviewConfig
        from app.agents.review.graph import review
        from app.agents.review.provider import ManualProvider

        runtime = ManualProvider(ReviewConfig.load(), env={"ANTHROPIC_MODEL": "test-model"})
        result = review(
            {
                **self.state,
                "carrier": "Test",
                "framework": "gathers",
                "check_passed": True,
                "syntax_passed": True,
                "attempt": 1,
                "problem": {
                    **self.state["problem"],
                    "service_name": "test",
                    "biztype": "tracking",
                    "carrier": "Test",
                },
            },
            runtime,
        )
        self.assertEqual(result["verdict"], "MANUAL")
        self.assertEqual(result["contract_error"], "")
        self.assertEqual(result["code_verdict"], "UNKNOWN")
        self.assertFalse(result["passed"])
        self.assertFalse(result["delivery_eligible"])
        self.assertEqual(set(result["dimensions"]), {"syntax", "business", "scope"})
        self.assertTrue(all(item["status"] == "unverified" for item in result["dimensions"].values()))

    def test_agent_request_failure_can_deliver_after_evidence_review(self):
        self.state["review_mode"] = "agent"
        self.state["regression_result"].update(passed=False, status="failed", reason="proxy timeout")
        self.state["review"] = self.review_report()
        self.assertEqual(decide(self.state)["action"], "deliver")

    def test_agent_request_failure_without_review_does_not_repair(self):
        self.state["review_mode"] = "agent"
        self.state["regression_result"].update(passed=False, status="failed", reason="403")
        result = decide(self.state)
        self.assertEqual((result["action"], result["target"]), ("retry", "review_agent"))

    def test_agent_confirmed_business_defect_repairs(self):
        self.state["review_mode"] = "agent"
        review = self.review_report()
        review.update(verdict="FAIL", passed=False, code_verdict="FAIL", findings=["parse 分类错误"])
        review["dimensions"]["business"]["status"] = "failed"
        self.state["review"] = review
        self.assertEqual(decide(self.state)["action"], "repair")

    def test_agent_uncertain_network_cause_blocks_without_code_changes(self):
        self.state["review_mode"] = "agent"
        review = self.review_report()
        review.update(verdict="MANUAL", passed=False, code_verdict="UNKNOWN")
        review["dimensions"]["business"]["status"] = "unverified"
        self.state["review"] = review
        self.assertEqual(decide(self.state)["action"], "blocked")

    def test_agent_cannot_override_stale_patch(self):
        self.state["review_mode"] = "agent"
        self.state["review"] = self.review_report()
        (self.workspace / self.state["changed_files"][0]).write_text("def parse(): return 5\n", encoding="utf-8")
        self.assertEqual(decide(self.state)["target"], "review_agent")

    def test_agent_contract_requires_three_dimensions_with_evidence(self):
        from app.agents.review.contract import judge
        review = self.review_report()
        self.assertTrue(judge(review).passed)
        review["dimensions"]["business"]["evidence"] = []
        self.assertFalse(judge(review).valid)
        del review["dimensions"]["scope"]
        self.assertFalse(judge(review).valid)

    def test_failed_issue_still_reaches_agent(self):
        trace = self.graph_trace(issue_failure=True, human=False)
        self.assertNotIn("run_regression", trace)
        self.assertIn("review_agent", trace)
        self.assertLess(trace.index("repair_agent"), trace.index("review_agent"))

    def test_reject_passed_when_execution_failed(self):
        with self.assertRaises(ValidationError):
            StepOutcome(
                execution=ExecutionResult(status="waiting"),
                assessment=Assessment(outcome="passed", code="bad", summary=""),
                output={},
            )

    def test_environment_failure_is_inconclusive(self):
        result = outcome_of(
            {},
            execution_status="failed",
            error={"code": "TIMEOUT", "message": "timeout", "retryable": True},
        )
        self.assertEqual(result.assessment.outcome, "inconclusive")

    def test_business_failure_is_successfully_executed(self):
        result = outcome_of({"passed": False, "status": "failed"})
        self.assertEqual(result.execution.status, "succeeded")
        self.assertEqual(result.assessment.outcome, "failed")

    def test_contract_survives_changed_file_subset(self):
        self.state["changed_files"] = []
        self.assertEqual(
            validate_acceptance(self.state).contract_id,
            self.state["acceptance_contract"]["contract_id"],
        )

    def test_contract_reports_content_tamper(self):
        self.state["acceptance_contract"]["assertions"][0]["required"] = False
        with self.assertRaisesRegex(ValueError, "ACCEPTANCE_CHANGED"):
            validate_acceptance(self.state)

    def test_contract_reports_changed_problem(self):
        self.state["problem"]["response"] = "different"
        with self.assertRaisesRegex(ValueError, "PROBLEM_CHANGED"):
            validate_acceptance(self.state)

    def test_contract_reports_changed_baseline(self):
        (self.baseline / self.state["changed_files"][0]).write_text(
            "changed", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "BASELINE_CHANGED"):
            validate_acceptance(self.state)

    def test_plan_requires_pre_repair_reproduction(self):
        self.state["baseline_reproduction"]["status"] = "unverified"
        with self.assertRaisesRegex(ValueError, "BASELINE_NOT_REPRODUCED"):
            make_plan(self.state)

    def test_human_mode_requires_validation_but_not_ai_review(self):
        self.assertEqual(decide(self.state)["action"], "deliver")
        self.state["regression_result"]["status"] = "not_configured"
        self.assertEqual(decide(self.state)["action"], "blocked")
        self.assertEqual(
            [item.name for item in dependencies_for("decide_validation", "human")],
            ["review_agent"],
        )

    def test_issue_failure_repairs_without_running_regression(self):
        self.state["verify_result"].update(passed=False, status="failed")
        self.state["regression_result"] = {}
        self.assertFalse(summarize(self.state)["stale"])
        self.assertEqual(decide(self.state)["action"], "repair")

    def test_changed_patch_invalidates_validation(self):
        (self.workspace / self.state["changed_files"][0]).write_text(
            "def parse(): return 2\n", encoding="utf-8"
        )
        result = decide(self.state)
        self.assertEqual(
            (result["action"], result["target"]), ("retry", "review_agent")
        )

    def test_verification_retry_has_separate_limit(self):
        self.state["verify_result"].update(passed=False, status="unverified")
        self.state["validation_recovery_counts"] = {"review_agent": 2}
        self.assertEqual(decide(self.state)["action"], "blocked")

    def test_handoff_preserves_full_text_and_invalidates_approval(self):
        store = MemoryStore()
        output = {"analysis": "a" * 5000}
        state = {
            **self.state,
            "approval_granted": True,
            "approval_result": {"decision": "APPROVE"},
            "delivery_candidate": {"commit": "old"},
        }
        update = apply_step_result(
            store, state, "repair_agent", {"repair": output}, outcome_of(output)
        )
        self.assertEqual(update["approval_result"], {})
        self.assertFalse(update["approval_granted"])
        saved = Handoff.model_validate(
            store.values["run", "task", "workflow/state.json"]
        )
        self.assertEqual(len(saved.outcome.output["analysis"]), 5000)
        self.assertEqual(saved.state.artifacts.repair, output)

    def test_assertion_summary_is_explicit(self):
        report = summarize(self.state)["report"]
        self.assertEqual(
            {row["assertion_id"] for row in report["results"]},
            {"input", "baseline", "fixed", "request"},
        )
        self.assertTrue(all(row["status"] == "passed" for row in report["results"]))

    def graph_trace(self, *, issue_failure=False, analysis=False, human=True):
        trace = []
        state = {**self.state, "review_mode": "human" if human else "agent"}

        def node(name):
            def run(current):
                trace.append(name)
                result = {"last_result": "passed"}
                if name == "locate_agent":
                    result["locate"] = {
                        **state["locate"],
                        **(
                            {"status": "MANUAL", "recommended_scope": "manual"}
                            if analysis
                            else {}
                        ),
                    }
                elif name == "verify_issue" and issue_failure:
                    result["verify_result"] = {
                        **state["verify_result"],
                        "passed": False,
                        "status": "failed",
                    }
                elif name == "summarize_validation":
                    result["validation_summary"] = summarize(current)
                elif name == "review_agent":
                    result["review"] = self.review_report()
                    if issue_failure:
                        result["verify_result"] = {**state["verify_result"], "passed": False, "status": "failed"}
                elif name == "decide_validation":
                    decision = decide(current)
                    result.update(
                        validation_decision=decision, retry_target=decision["target"]
                    )
                elif name == "request_approval":
                    result.update(approval_granted=True, decision="APPROVE")
                elif name == "finalize_execution":
                    result["task_summary"] = {"finished": True}
                return result

            return run

        graph = build_task_graph(
            {name: node(name) for name in TASK_NODE_NAMES},
            retry_context_builder=lambda value: {},
        )
        # A failure at the repair limit should finalize without entering a retry loop.
        if issue_failure:
            state["automatic_attempt"] = state["max_attempts"]
        graph.invoke(state)
        return trace

    def test_human_success_path_approves_before_publish(self):
        trace = self.graph_trace()
        self.assertIn("review_agent", trace)
        self.assertLess(trace.index("reproduce_baseline"), trace.index("repair_agent"))
        self.assertLess(
            trace.index("request_approval"), trace.index("publish_delivery")
        )
        self.assertEqual(trace.count("finalize_execution"), 1)

    def test_agent_success_path_runs_review(self):
        self.assertIn("review_agent", self.graph_trace(human=False))

    def test_failed_issue_collects_regression_before_human_decision(self):
        trace = self.graph_trace(issue_failure=True)
        self.assertNotIn("run_regression", trace)
        self.assertIn("review_agent", trace)
        self.assertNotIn("prepare_delivery", trace)

    def test_analysis_is_delivered_before_finalization(self):
        trace = self.graph_trace(analysis=True)
        self.assertNotIn("repair_agent", trace)
        self.assertNotIn("request_approval", trace)
        self.assertLess(
            trace.index("prepare_analysis"), trace.index("prepare_delivery")
        )
        self.assertLess(
            trace.index("prepare_delivery"), trace.index("finalize_execution")
        )
        self.assertEqual(trace.count("finalize_execution"), 1)

    def test_real_runners_persist_restore_and_approve_same_candidate(self):
        """使用正式 Runner、RecordStore 和步骤，仅替换外部执行器。"""
        from app.application.services import WorkflowServices
        from app.application.step_runner import state_from_records
        from app.infrastructure.records.store import RecordStore
        from app.workflow.plugin_loader import load_all
        from app.workflow.registry import resolve
        from app.workflow.runner import bind
        from app.agents.review.tools.run_regression.regression import RegressionRunner

        load_all()
        store = RecordStore(self.root / "runtime")
        store.create_run(
            "run", {"workflow_version": 7, "review_mode": "human", "max_attempts": 3}
        )
        state = {
            **self.state,
            "task": {
                "carrier": "Test",
                "biztype": "framework.track_server",
                "service_name": "gather.py.track",
                "problem_key": "problem",
                "problem": self.state["problem"],
            },
            "source_repo_path": str(self.root),
            "source_project_path": str(self.root),
            "allow_push": True,
            "evidence": {"carrier_dir": "gathers/Test"},
            "repair": {"summary": "fix"},
        }
        calls = []

        class Delivery:
            def deliver(self, incoming):
                calls.append(incoming)
                incoming["persist_delivery"](
                    {"commit": "a" * 40, "repair_branch": incoming["repair_branch"]}
                )
                return {
                    "commit": "a" * 40,
                    "repair_branch": incoming["repair_branch"],
                    "pushed": incoming["allow_push"],
                    "local_branch": not incoming["allow_push"],
                }

        services = WorkflowServices(
            source_repo=self.root,
            delivery=Delivery(),
            workspaces=SimpleNamespace(project_root=lambda source: self.root),
            metadata={
                "independent_verify": lambda payload: (
                    {"passed": True, "reason": "baseline reproduced"}
                    if payload.get("baseline_only")
                    else self.issue_report()
                ),
                "request_regression": lambda *a, **kw: {},
            },
            verify_commands=[["python", "test.py"]],
        )

        def run(name):
            from app.agents.review.tools.registry import resolve_tool
            from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
            spec = resolve_tool(name) if name in REVIEW_EVIDENCE_STEPS else resolve(name)
            state.update(bind(spec, store, services)(state))

        run("define_acceptance")
        run("reproduce_baseline")
        # Real check steps are exercised separately; inject successful evidence as the platform would.
        state.update(
            gate_result=self.state["gate_result"], syntax_result={"passed": True}
        )
        run("plan_validation")
        run("verify_issue")
        request_result = {
            "passed": True,
            "status": "verified",
            "entrypoint": "server_dev.main",
            "commands": [{"command": ["python", "request.py"], "returncode": 0}],
        }
        with patch.object(
            RegressionRunner,
            "run",
            return_value={
                "passed": True,
                "regression": {
                    "results": request_result["commands"],
                    "request_regression": request_result,
                },
            },
        ):
            run("run_regression")
        run("summarize_validation")
        run("decide_validation")
        self.assertEqual(state["validation_decision"]["action"], "deliver")
        run("prepare_delivery")
        self.assertFalse(calls[0]["allow_push"])
        candidate = state["delivery_candidate"]["candidate_id"]
        state["decision"] = "APPROVE"
        with patch(
            "app.workflow.steps.request_approval.node.final_gate",
            return_value={"passed": True},
        ):
            run("request_approval")
        self.assertEqual(state["approval_result"]["candidate_id"], candidate)
        run("publish_delivery")
        self.assertTrue(calls[-1]["allow_push"])
        self.assertEqual(state["delivery"]["candidate_id"], candidate)
        restored = state_from_records(store, "run", "task")
        self.assertEqual(restored["delivery"]["commit"], "a" * 40)
        self.assertEqual(restored["acceptance_contract"], state["acceptance_contract"])
        run("finalize_execution")
        self.assertEqual(state["resolution"]["result"], "delivered")

    def test_publish_rejects_changed_candidate_or_unapproved_state(self):
        from app.domain.errors import StepFailed
        from app.workflow.steps.prepare_delivery.models import DeliveryInput
        from app.workflow.steps.prepare_delivery.node import run

        data = DeliveryInput(
            task_id="task",
            carrier="Test",
            repair_branch="branch",
            ai_workspace_path=str(self.workspace),
            changed_files=self.state["changed_files"],
            binding=self.state["validation_plan"]["binding"],
            publish=True,
            approved_candidate_id="expired",
        )
        with self.assertRaisesRegex(StepFailed, "审批未绑定"):
            run(SimpleNamespace(), data)

    def test_identification_creates_one_task_per_biztype_and_carrier(self):
        from app.workflow.steps.identify_carrier.models import IdentifyCarrierInput
        from app.workflow.steps.identify_carrier.node import run

        problems = [
            {
                "service_name": "gather.py.track",
                "biztype": "framework.track_server",
                "carrier": "Test",
                "req": "{}",
                "error": message,
            }
            for message in ("ValueError: one", "TypeError: two")
        ]
        # 不同 carrier 不受同组合并影响，仍各自建任务。
        problems.append({**problems[0], "carrier": "Other"})
        records = [
            {
                **problem,
                "problem": problem,
                "dedup_key": str(i),
                "representative_index": i,
            }
            for i, problem in enumerate(problems)
        ]
        with (
            patch(
                "app.workflow.steps.identify_carrier.node.records_of",
                return_value=records,
            ),
            patch(
                "app.workflow.steps.identify_carrier.node.is_registered_service",
                return_value=True,
            ),
        ):
            result = run(SimpleNamespace(), IdentifyCarrierInput(run_id="run"))
        self.assertEqual(result.created, 2)
        by_carrier = {task.carrier: task for task in result.tasks}
        # 同一 (biztype, carrier) 只保留最早出现（representative_index 最小）的异常。
        self.assertEqual(by_carrier["Test"].problem["error"], "ValueError: one")

    def test_agent_input_failure_is_recorded_without_escaping_task(self):
        from app.infrastructure.records.store import RecordStore
        from app.workflow.agent_adapter import _agent_node
        from app.workflow.plugin_loader import load_all
        from app.workflow.registry import resolve

        load_all()
        store = RecordStore(self.root / "agent-runtime")
        store.create_run("run", {"workflow_version": 7})
        result = _agent_node(resolve("repair_agent"), SimpleNamespace(), store)(
            self.state
        )
        self.assertEqual(result["last_result"], "failed")
        formal = Handoff.model_validate(
            store.read_artifact("run", "workflow/state.json", "task")
        )
        self.assertEqual(formal.outcome.execution.status, "failed")
        self.assertEqual(formal.outcome.assessment.outcome, "inconclusive")

    def test_publish_retry_preserves_review_approval_and_candidate(self):
        observed = []
        store = MemoryStore()
        nodes = {
            name: lambda state: {"last_result": "passed"} for name in TASK_NODE_NAMES
        }

        def publish(state):
            observed.append(state)
            return {"last_result": "passed"}

        nodes["publish_delivery"] = publish
        state = {
            **self.state,
            "retry_target": "publish_delivery",
            "approval_granted": True,
            "review": {"verdict": "PASS"},
            "approval_result": {"decision": "APPROVE", "candidate_id": "current"},
            "delivery_candidate": {"candidate_id": "current"},
            "validation_decision": {"delivery_eligible": True, "action": "deliver"},
        }
        graph = build_task_graph(
            nodes,
            transition_store=store,
            entry_node="retry_prepare",
            retry_context_builder=lambda value: {},
        )
        graph.invoke(state)
        self.assertTrue(observed[0]["validation_decision"]["delivery_eligible"])
        self.assertTrue(observed[0]["approval_granted"])
        self.assertEqual(observed[0]["review"]["verdict"], "PASS")
        self.assertEqual(observed[0]["delivery_candidate"]["candidate_id"], "current")
        self.assertIn(
            ("run", "task", "workflow/outputs/retry_prepare.json"), store.values
        )

    def test_review_pipeline_keeps_evidence_inside_single_agent(self):
        from app.workflow.agent_adapter import build_agent_node
        from app.application.services import WorkflowServices
        from app.infrastructure.records.store import RecordStore
        from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
        from app.workflow.plugin_loader import load_all
        from app.workflow.registry import resolve
        from app.agents.review.tools.run_regression.regression import RegressionRunner

        load_all()
        store = RecordStore(self.root / "pipeline-runtime")
        store.create_run("run", {"workflow_version": 7, "review_mode": "agent", "max_attempts": 3})
        state = {
            **self.state, "review_mode": "agent", "framework": "gathers",
            "task": {"carrier": "Test", "biztype": "tracking", "service_name": "test", "problem_key": "problem"},
            "source_repo_path": str(self.root), "source_project_path": str(self.root),
            "evidence": {"carrier_dir": "gathers/Test"}, "repair": {"summary": "fix"},
        }
        services = WorkflowServices(
            source_repo=self.root, workspaces=SimpleNamespace(project_root=lambda source: self.root),
            metadata={"independent_verify": lambda payload: self.issue_report(), "request_regression": lambda *a, **kw: {}},
            verify_commands=[["python", "test.py"]],
        )
        observed = []

        def model(request):
            observed.append(request.prompt_context)
            return self.review_report()

        pipeline = build_agent_node(resolve("review_agent"), store,
                                    SimpleNamespace(run=model, env={"ANTHROPIC_MODEL": "test-model"}), services=services)
        request = {"passed": False, "status": "failed", "reason": "规则服务 HTTP 403", "entrypoint": "server_dev.main"}
        with patch.object(RegressionRunner, "run", return_value={
            "passed": False, "reason": "规则服务 HTTP 403",
            "regression": {"results": [{"command": ["python", "request.py"], "returncode": 0}], "request_regression": request},
        }):
            update = pipeline(state)
        self.assertEqual([item.name for item in store.list_steps("run", "task")], ["review_agent"])
        self.assertEqual([item["name"] for item in update["review"]["substeps"]], list(REVIEW_EVIDENCE_STEPS))
        self.assertEqual(observed[0]["regression_results"]["status"], "failed")
        self.assertEqual(update["review"]["verdict"], "PASS")
        self.assertEqual(decide({**state, **update})["action"], "deliver")
        self.assertIsNotNone(store.load("run", "verify_issue", "task"))
        self.assertTrue(any(ref.name == "verification_report" for ref in store.load("run", "review_agent", "task").artifacts))
        self.assertEqual(len(store.read_artifact("run", "artifacts/review_agent/substeps.json", "task")["substeps"]), 6)

    def test_main_graph_has_no_legacy_review_nodes(self):
        from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
        from workbench.projections import task_stages
        self.assertFalse(set(TASK_NODE_NAMES) & set(REVIEW_EVIDENCE_STEPS))
        self.assertNotIn("validate_changes", TASK_NODE_NAMES)
        self.assertEqual([stage["key"] for stage in task_stages()].count("review_agent"), 1)

    def test_local_git_candidate_and_publish_are_idempotent(self):
        """真实临时 Git 仓库与 bare remote，不接触配置中的远程。"""
        from git import Repo

        from app.infrastructure.filesystem.workspace import (
            WorkspaceConfig,
            WorkspaceManager,
        )
        from app.infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig

        source = Repo.init(self.baseline, initial_branch="main")
        with source.config_writer() as config:
            config.set_value("user", "name", "Workflow Test")
            config.set_value("user", "email", "workflow@example.invalid")
        source.git.add(".")
        source.index.commit("baseline")
        remote = Repo.init(self.root / "remote.git", bare=True)
        source.create_remote("origin", str(remote.git_dir))
        source.git.push("origin", "main")
        branch = "ai-bug-tasks/framework.track_server/Test-abcdef12"
        manager = WorkspaceManager(WorkspaceConfig(root_dir=self.root / "workspaces"))
        delivery = GitBranchDelivery(
            GitDeliveryConfig(source_repo=self.baseline, workspace_manager=manager)
        )
        receipts = []
        incoming = {
            "run_id": "run",
            "task_id": "task",
            "carrier": "Test",
            "repair_branch": branch,
            "source_repo_path": str(self.baseline),
            "ai_workspace_path": str(self.workspace),
            "changed_files": self.state["changed_files"],
            "allow_push": False,
            "persist_delivery": receipts.append,
        }
        try:
            candidate = delivery.deliver(incoming)
            self.assertFalse(candidate["pushed"])
            self.assertEqual(remote.git.for_each_ref(f"refs/heads/{branch}"), "")
            published = delivery.deliver(
                {**incoming, "allow_push": True, "delivery_receipt": receipts[-1]}
            )
            repeated = delivery.deliver(
                {**incoming, "allow_push": True, "delivery_receipt": receipts[-1]}
            )
            self.assertEqual(candidate["commit"], published["commit"])
            self.assertEqual(published["commit"], repeated["commit"])
            self.assertEqual(
                remote.commit(f"refs/heads/{branch}").hexsha, candidate["commit"]
            )
        finally:
            source.close()
            remote.close()

    def test_state_ownership_rejects_agent_baseline_write(self):
        with self.assertRaisesRegex(ValueError, "STATE_OWNER_MISMATCH"):
            apply_step_result(
                MemoryStore(),
                self.state,
                "repair_agent",
                {"baseline_dir": "different"},
                outcome_of({}),
            )

    def test_services_and_full_graph_build_without_network(self):
        from dataclasses import replace

        from app.infrastructure.config import load_config
        from app.workflow.wiring import build_graph, build_services, close_checkpoints

        config = replace(
            load_config(),
            log_es="",
            runtime_dir=self.root / "build-runtime",
            source_repo=self.root,
            source_url="",
        )
        services = build_services(config=config)
        self.assertTrue(callable(services.metadata["independent_verify"]))
        try:
            graph = build_graph(config=config, services=services)
            self.assertIsNotNone(graph)
        finally:
            close_checkpoints()

    def test_candidate_failure_cannot_retry_forever(self):
        from app.workflow.definitions import route_candidate

        state = {
            **self.state,
            "last_result": "failed",
            "retry_target": "prepare_delivery",
            "validation_recovery_counts": {"prepare_delivery": 2},
        }
        self.assertEqual(route_candidate(state), "finalize_execution")

    def test_stage_retry_does_not_consume_repair_budget(self):
        from app.workflow.routes import _retry_or_finalize

        state = {
            **self.state,
            "automatic_attempt": 3,
            "retry_target": "claim_repair_slot",
            "validation_recovery_counts": {"claim_repair_slot": 0},
        }
        self.assertEqual(_retry_or_finalize(state), "retry_prepare")
        state["retry_target"] = "repair_agent"
        self.assertEqual(_retry_or_finalize(state), "finalize_execution")


if __name__ == "__main__":
    unittest.main()
