"""单个异常点的全链路测试。

链路：ES message 准入 → 规范化 → 去重 → 派单 → 证据准备 → 修复 Agent → 审查
      → 报告生成 → 交付（准备/正式）→ 收尾。

只在两处打桩：模型调用（agent_runner / provider.run）与远端 git 交付服务。
其余全部走真实代码：解析、白名单校验、AST 自检、审查契约、报告构建、
候选身份校验、交付回执与终态汇总。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.review.contract import REVIEW_CONTRACT_VERSION, ReviewConfig
from agents.review.graph import review as review_run
from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from core.contracts import ArtifactRef, StepRecord
from infrastructure.es.logs import ESLogAdapter
from infrastructure.git.delivery import GitBranchDelivery
from infrastructure.storage.store import RecordStore
from workflow.engine.evidence_version import evidence_version
from workflow.engine.routes import _analysis_only_task
from workflow.stages.log_pipeline.deduplicate_error.node import write_groups
from workflow.stages.log_pipeline.identify_carrier.models import IdentifyCarrierInput
from workflow.stages.log_pipeline.identify_carrier.node import run as identify_run
from workflow.stages.log_pipeline.normalize_error.node import _normalize
from workflow.stages.prepare_context.prepare_evidence.models import EvidenceInput
from workflow.stages.prepare_context.prepare_evidence.node import run as evidence_run
from workflow.stages.generate_report import _input as report_input_builder
from workflow.stages.generate_report.models import DeliveryInput
from workflow.stages.generate_report.node import run as report_run
from workflow.stages.finalize.models import FinalizeInput
from workflow.stages.finalize.node import run as finalize_run
from workflow.stages.submit_changes import input_of as submit_input_builder
from workflow.stages.submit_changes import run as submit_run

RUN_ID = "run-e2e"
CARRIER = "TEST"
BIZTYPE = "framework.track_server"
SERVICE = "gather.py.track"
CHANGED = "gathers/TEST/main.py"

BROKEN_SOURCE = """\
async def read_html(self, track_model):
    return track_model.response


async def process_html(self, track_model):
    items = track_model.response.get("items")
    return items[0]
"""

FIXED_SOURCE = """\
async def read_html(self, track_model):
    return track_model.response


async def process_html(self, track_model):
    items = track_model.response.get("items")
    if not items:
        return TrackResponseModel.on_all_errors(
            ErrorType.ServiceError, track_model, error_msg="empty items"
        )
    return items[0]
"""

LOG_LINE = (
    "2026-10-29 10:00:00,000 [1] ERROR gather.py.track - framework.track_server:"
    '采集执行异常 req:{"carrier":"TEST","requests":[{"number":"N1"}]} '
    "error: builtins.IndexError, list index out of range "
    "file: main.py, line: 41, function: process_html"
)

FIX_PAYLOAD = {
    "status": "FIXED",
    "root_cause": "【代码问题】items 为空时直接取下标触发 IndexError",
    "category": "carrier",
    "attribution_type": "code",
    "facts": ["track_model.response 的 items 可能为空"],
    "source_evidence": [{
        "path": CHANGED, "symbol": "TestGather.process_html",
        "finding": "items[0] 未做空列表判断", "start_line": 7, "end_line": 7,
    }],
    "candidate_files": [CHANGED],
    "proposed_edit_files": [CHANGED],
    "changed_files": [CHANGED],
    "change_plan": {
        "scope": "carrier",
        "rationale": "空列表时返回 ServiceError，避免裸抛 IndexError",
        "edits": [{"path": CHANGED, "symbol": "process_html", "validation_notes": "空列表分支"}],
    },
    "acceptance_criteria": ["空 items 不再抛 IndexError"],
    "self_check": {"syntax_valid": True, "reflection_notes": "AST 自检通过"},
    "evidence_gaps": [],
    "blocked_reason": "",
    "scope_escalation_required": False,
    "recommended_scope": "carrier",
    "summary": "【分流】代码问题 【修复目标】空列表保护",
    "analysis": "静态分析确认",
    "passed": True,
    "result": "passed",
}

REVIEW_PAYLOAD = {
    "verdict": "PASS",
    "passed": True,
    "code_verdict": "PASS",
    "coverage": {"decision": "sufficient", "reason": "改动单点可核对",
                 "reviewed_symbols": [CHANGED], "missing_checks": []},
    "dimensions": {
        "syntax": {"status": "passed", "reason": "语法正确", "evidence": [CHANGED]},
        "business": {"status": "passed", "reason": "空列表分支正确", "evidence": [CHANGED]},
        "scope": {"status": "passed", "reason": "只改当前 carrier", "evidence": [CHANGED]},
    },
    "environment_assessment": "无需环境验证",
    "upstream_assessment": "与上游无关",
    "summary": "审查通过",
    "findings": [],
    "required_changes": [],
    "todo": [],
}


class RecordingDelivery:
    """记录交付调用并写回执，不触碰远端 git。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def deliver(self, state: dict) -> dict:
        self.calls.append(state)
        persist = state.get("persist_delivery")
        if callable(persist):
            persist({"commit": "deadbeef", "pushed": False, "delivery_phase": "DELIVERED"})
        return {
            "repair_branch": str(state.get("repair_branch") or ""),
            "commit": "deadbeef",
            "pushed": False,
            "local_branch": True,
        }


def _workspace(root: Path) -> tuple[Path, Path]:
    """准备隔离工作区与只读基线（各含一个 carrier 源码）。"""
    workspace = root / "workspace"
    baseline = root / "baseline"
    for base in (workspace, baseline):
        carrier_dir = base / "gathers" / CARRIER
        carrier_dir.mkdir(parents=True)
        (carrier_dir / "main.py").write_text(BROKEN_SOURCE, encoding="utf-8")
    return workspace, baseline


class ManualReviewDeliveryTests(unittest.TestCase):
    """缺录制 response → 审查 MANUAL（业务未验证）→ 仍必须交付代码。"""

    def _state(self, root: Path, workspace: Path, baseline: Path) -> dict:
        version = evidence_version(str(workspace), [CHANGED], str(baseline))
        manual_review = {
            **REVIEW_PAYLOAD,
            "verdict": "MANUAL", "passed": False,
            "summary": "代码正确，但缺少原始 response 无法重放业务回归",
            "evidence_version": version,
            "review_contract_version": REVIEW_CONTRACT_VERSION,
        }
        return {
            "run_id": "run-manual", "task_id": "task-manual", "attempt": 1,
            "task": {"task_id": "task-manual", "carrier": CARRIER,
                     "biztype": BIZTYPE, "problem_key": "k" * 24},
            "problem": {"service_name": SERVICE, "biztype": BIZTYPE,
                        "carrier": CARRIER, "req": "{}", "error": "IndexError"},
            "evidence": {"framework": "gathers", "carrier_dir": f"gathers/{CARRIER}"},
            "unified_fixer": {
                "status": "FIXED", "recommended_scope": "carrier", "category": "carrier",
                "analysis": "空列表保护缺失", "summary": "已补空列表分支",
            },
            "review": manual_review,
            "ai_workspace_path": str(workspace),
            "baseline_dir": str(baseline),
            "changed_files": [CHANGED],
            "agent_policy": {}, "review_mode": "agent", "framework": "gathers",
        }

    def test_manual_review_is_not_analysis_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, baseline = _workspace(root)
            (workspace / CHANGED).write_text(FIXED_SOURCE, encoding="utf-8")
            state = self._state(root, workspace, baseline)
            self.assertFalse(
                _analysis_only_task(state),
                "MANUAL 只表示业务未验证，不应降级为只交报告",
            )

    def test_manual_review_delivers_code_with_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, baseline = _workspace(root)
            (workspace / CHANGED).write_text(FIXED_SOURCE, encoding="utf-8")
            state = self._state(root, workspace, baseline)
            data = DeliveryInput.model_validate(report_input_builder(state))
            self.assertFalse(data.analysis_only)
            self.assertTrue(data.delivery_eligible, "MANUAL 仍在允许交付的结论集合内")

            delivery = RecordingDelivery()
            ctx = SimpleNamespace(
                store=RecordStore(root / "runtime"), run=SimpleNamespace(run_id="run-manual"),
                task=SimpleNamespace(task_id="task-manual", carrier=CARRIER, biztype=BIZTYPE),
                services=SimpleNamespace(delivery=delivery), artifacts=[],
            )
            output = report_run(ctx, data)
            self.assertEqual(output.delivery_phase, "PREPARED")
            self.assertTrue(output.manual_confirmation_required, "必须标记人工复核")
            self.assertEqual(delivery.calls[0]["changed_files"], [CHANGED], "代码改动必须进入交付")


class ReportDeliveryFormatTests(unittest.TestCase):
    """分支上的报告是 Markdown（task_summary.md），内部 final.json 不进分支。"""

    def test_final_json_is_rendered_as_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / "workspace", root / "delivery"
            report_path = source / "gathers" / CARRIER / "final.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps({
                "task_id": "task", "carrier": CARRIER, "biztype": BIZTYPE,
                "result": "MANUAL", "problem": "builtins.IndexError",
                "root_cause": "items 为空时直接取下标",
                "change_summary": "补充空列表分支",
                "review_decision": "MANUAL",
                "review": {"verdict": "MANUAL",
                           "dimensions": {"business": {"status": "unverified"}}},
                "manual_confirmation_required": True,
                "evidence_gaps": ["缺少原始 response"],
            }, ensure_ascii=False), encoding="utf-8")
            (dest / "gathers" / CARRIER).mkdir(parents=True)

            copied = GitBranchDelivery._copy_reports(
                source, dest, [f"gathers/{CARRIER}/final.json"],
                carrier=CARRIER, repair_branch="repair/TEST",
            )

            self.assertEqual(copied, [f"gathers/{CARRIER}/task_summary.md"])
            markdown = (dest / f"gathers/{CARRIER}/task_summary.md").read_text(encoding="utf-8")
            self.assertTrue(markdown.startswith("#"), "交付报告必须是 Markdown")
            self.assertIn("需人工处理", markdown)
            self.assertIn("repair/TEST", markdown)
            # 需要人工确认的交付：待办必须直接落在交付物里，而不是靠流程里的"确认步骤"。
            self.assertIn("## TODO（需人工确认）", markdown)
            self.assertIn("- [ ] 缺少原始 response", markdown)
            self.assertIn("- [ ] 业务回归未验证", markdown)
            self.assertFalse(
                (dest / f"gathers/{CARRIER}/final.json").exists(),
                "内部 final.json 不得进入分支",
            )

    def test_unsafe_report_paths_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / "workspace", root / "delivery"
            (source / "gathers" / CARRIER).mkdir(parents=True)
            (source / "gathers" / CARRIER / "final.json").write_text("{}", encoding="utf-8")
            (dest / "gathers" / CARRIER).mkdir(parents=True)
            copied = GitBranchDelivery._copy_reports(
                source, dest,
                [f"gathers/{CARRIER}/final.json", "artifacts/final.json", "../escape.md"],
                carrier=CARRIER,
            )
            self.assertEqual(copied, [f"gathers/{CARRIER}/task_summary.md"])


class SingleAnomalyEndToEndTests(unittest.TestCase):
    def test_one_anomaly_walks_the_whole_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, baseline = _workspace(root)
            store = RecordStore(root / "runtime")

            # ── 1. 准入 ────────────────────────────────────────────────
            row = ESLogAdapter.parse_hit({"_source": {
                "service_name": SERVICE, "biztype": BIZTYPE, "message": LOG_LINE,
            }})
            self.assertIsNotNone(row, "单条异常必须能被准入解析")
            self.assertEqual(row["carrier"], CARRIER)
            self.assertEqual(row["message_format"], "req")

            # ── 2. 规范化 ──────────────────────────────────────────────
            normalized = _normalize(row, 0)
            self.assertEqual(normalized["exception_class"], "builtins.IndexError")
            self.assertEqual(normalized["frame"], "main.py:41:process_html")
            self.assertEqual(normalized["blame"], "code")

            # ── 3. 去重 ────────────────────────────────────────────────
            groups = write_groups(iter([normalized]), ())
            self.assertEqual(len(groups["records"]), 1)
            self.assertEqual(groups["ignored_count"], 0)

            # ── 4. 派单（写 normalize_error 记录，identify_carrier 依赖它）──
            counts = {
                "record_count": groups["input_count"],
                "ignored_count": groups["ignored_count"],
                "duplicate_count": groups["duplicate_count"],
                "unique_count": len(groups["records"]),
            }
            context = SimpleNamespace(
                store=store,
                run=SimpleNamespace(run_id=RUN_ID),
                task=SimpleNamespace(task_id="", biztype=BIZTYPE, carrier=CARRIER),
                services=SimpleNamespace(),
                artifacts=[],
            )
            context.load_artifact = lambda ref: store.read_artifact(RUN_ID, ref.path, ref.task_id)

            def emit(name, relative, value):
                path = store.write_artifact(RUN_ID, relative, value, "")
                item = ArtifactRef(name=name, path=path, task_id="")
                context.artifacts.append(item)
                return item

            context.emit_artifact = emit
            ref = context.emit_artifact(
                "normalized_records", "artifacts/normalized_records.json",
                {"records": groups["records"], **counts},
            )
            store.save(RUN_ID, StepRecord(
                name="normalize_error", title="规范化与去重", status="succeeded",
                attempt=1, output=dict(counts), artifacts=[ref],
            ), "")

            tasks = identify_run(context, IdentifyCarrierInput(run_id=RUN_ID)).tasks
            self.assertEqual(len(tasks), 1, "单个异常点应产生且只产生一个任务")
            task = tasks[0]
            problem = task.problem
            self.assertEqual(problem["blame"], "code")
            self.assertEqual(problem["exception_class"], "builtins.IndexError")
            self.assertEqual(problem["frame"], "main.py:41:process_html")

            # ── 5. 证据准备（真实扫描 carrier 目录）────────────────────
            evidence = evidence_run(context, EvidenceInput(
                task_id=task.task_id, carrier=CARRIER, biztype=BIZTYPE,
                service_name=SERVICE, problem=problem,
                workspace_path=str(workspace), framework="gathers",
            ))
            self.assertEqual(evidence.framework, "gathers")
            self.assertIn(CHANGED, evidence.candidates)

            # ── 6. 修复 Agent（真实白名单校验 + AST 自检，LLM 打桩）────
            def runner(**kwargs):
                (workspace / CHANGED).write_text(FIXED_SOURCE, encoding="utf-8")
                return {"result": json.dumps(FIX_PAYLOAD, ensure_ascii=False)}

            fixer_state = {
                "run_id": RUN_ID, "task_id": task.task_id, "attempt": 1,
                "carrier": CARRIER, "workspace_path": str(workspace),
                "problem": problem,
                "evidence": {**evidence.model_dump(), "framework": "gathers"},
                "agent_policy": {"unified_model": "test-model"},
                "source_snapshot": {},
                "source_project_path": "",
                "retry_reason": "",
            }
            runtime = SimpleNamespace(
                config=UnifiedFixerConfig(max_turns=4), agent_runner=runner,
            )
            with patch(
                "agents.unified_fixer.provider.invoke_with_audit",
                side_effect=lambda request, invoke: invoke(),
            ):
                fixed = run_unified_fixer(fixer_state, runtime)

            self.assertEqual(fixed["status"], "FIXED")
            self.assertTrue(fixed["passed"])
            self.assertEqual(fixed["changed_files"], [CHANGED])
            self.assertTrue(fixed["self_check"]["syntax_valid"])
            self.assertTrue((workspace / "artifacts" / "analysis.json").is_file())

            # ── 7. 审查（真实 diff + 契约校验，LLM 打桩）──────────────
            review_state = {
                "ai_workspace_path": str(workspace),
                "carrier": CARRIER, "framework": "gathers",
                "problem": problem,
                "run_id": RUN_ID, "task_id": task.task_id, "attempt": 1,
                "changed_files": [CHANGED],
                "baseline_dir": str(baseline),
                "baseline_manifest": {},
                "source_project_path": "",
                "diff_summary": "",
                "repair": fixed.get("unified_fixer") or fixed,
            }
            review_runtime = SimpleNamespace(
                env={"ANTHROPIC_MODEL": "test-model"},
                config=ReviewConfig(max_turns=2),
                run=lambda request: {
                    "result": json.dumps(REVIEW_PAYLOAD, ensure_ascii=False)
                },
            )
            verdict = review_run(review_state, review_runtime)
            self.assertEqual(verdict["verdict"], "MANUAL")
            self.assertFalse(verdict["passed"])
            self.assertEqual(verdict["code_verdict"], "PASS")
            self.assertEqual(verdict["dimensions"]["business"]["status"], "unverified")
            self.assertEqual(
                [item["key"] for item in verdict["checks"]],
                ["syntax", "business", "scope"],
            )
            self.assertEqual(len(verdict["diff_files"]), 1)

            # ── 8. 落 Agent 步骤记录（报告汇总依赖真实步骤记录）──────
            store.save(RUN_ID, StepRecord(
                name="unified_fixer_agent", title="统一分析修复", status="succeeded",
                attempt=1, output=fixed, summary=str(fixed.get("summary") or ""),
            ), task.task_id)
            store.save(RUN_ID, StepRecord(
                name="review_agent", title="修复后代码审查", status="succeeded",
                attempt=1, output=verdict, summary=str(verdict.get("summary") or ""),
            ), task.task_id)

            # ── 9. 报告生成 + 交付准备（publish=False）────────────────
            delivery = RecordingDelivery()
            flow_ctx = SimpleNamespace(
                store=store, run=SimpleNamespace(run_id=RUN_ID),
                task=SimpleNamespace(task_id=task.task_id, carrier=CARRIER, biztype=BIZTYPE),
                services=SimpleNamespace(delivery=delivery), artifacts=[],
            )
            delivery_state = {
                "run_id": RUN_ID, "task_id": task.task_id, "attempt": 1,
                "task": {"task_id": task.task_id, "carrier": CARRIER,
                         "biztype": BIZTYPE, "problem_key": task.problem_key},
                "problem": problem,
                "evidence": {**evidence.model_dump(), "framework": "gathers"},
                "unified_fixer": fixed.get("unified_fixer") or fixed,
                "review": verdict,
                "ai_workspace_path": str(workspace),
                "baseline_dir": str(baseline),
                "changed_files": list(fixed.get("changed_files") or []),
                "agent_policy": {}, "review_mode": "agent", "framework": "gathers",
            }
            prepared = report_run(
                flow_ctx, DeliveryInput.model_validate(report_input_builder(delivery_state))
            )
            self.assertEqual(prepared.delivery_phase, "PREPARED")
            self.assertFalse(prepared.pushed)
            task_dir = store.task_dir(RUN_ID, task.task_id)
            self.assertTrue((task_dir / "delivery" / "final.json").is_file(),
                            "必须生成最终报告")
            self.assertTrue((task_dir / "delivery" / "operation.json").is_file(),
                            "必须记录交付回执")

            # ── 10. 正式交付（publish=True，必须复用同一候选）──────────
            delivery_state["delivery_candidate"] = prepared.model_dump()
            submitted = submit_run(
                flow_ctx, DeliveryInput.model_validate(submit_input_builder(delivery_state))
            )
            self.assertEqual(submitted.delivery_phase, "DELIVERED")
            self.assertEqual(submitted.commit, "deadbeef")
            self.assertEqual(submitted.candidate_id, prepared.candidate_id,
                             "准备与正式交付必须是同一候选")
            self.assertEqual(len(delivery.calls), 2, "准备与正式交付各调用一次交付服务")

            # 落交付步骤记录：终态汇总靠它确认"提交确实落地"。
            store.save(RUN_ID, StepRecord(
                name="generate_report", title="汇总生成报告", status="succeeded",
                attempt=1, output=prepared.model_dump(), input={"analysis_only": False},
            ), task.task_id)
            store.save(RUN_ID, StepRecord(
                name="submit_changes", title="提交候选分支", status="succeeded",
                attempt=1, output=submitted.model_dump(),
            ), task.task_id)

            # ── 11. 收尾：汇总终态报告 ───────────────────────────────
            finalized = finalize_run(flow_ctx, FinalizeInput(
                task_id=task.task_id, carrier=CARRIER, biztype=BIZTYPE,
                terminal_status="succeeded", ai_workspace_path=str(workspace),
            ))
            self.assertIsNotNone(finalized.resolution)
            self.assertTrue(finalized.report_path, "收尾必须产出报告路径")
            self.assertEqual(
                str(finalized.resolution.result), "delivered",
                "提交已落地（commit + local_branch）时终态必须是 delivered",
            )

            # 设置 DSH_E2E_TRACE=1 可打印每一段的关键产物，便于人工核对链路。
            if os.environ.get("DSH_E2E_TRACE"):
                print(json.dumps({
                    "1_parse": {"carrier": row["carrier"], "format": row["message_format"]},
                    "2_normalize": {
                        "exception_class": normalized["exception_class"],
                        "frame": normalized["frame"], "blame": normalized["blame"],
                    },
                    "3_dedup": {k: groups[k] for k in
                                ("input_count", "ignored_count", "duplicate_count")},
                    "4_identify": {"tasks": len(tasks), "task_id": task.task_id},
                    "5_evidence": {"framework": evidence.framework,
                                   "candidates": evidence.candidates},
                    "6_fixer": {"status": fixed["status"], "passed": fixed["passed"],
                                "changed_files": fixed["changed_files"],
                                "syntax_valid": fixed["self_check"]["syntax_valid"]},
                    "7_review": {"verdict": verdict["verdict"],
                                 "code_verdict": verdict["code_verdict"],
                                 "checks": [c["status"] for c in verdict["checks"]]},
                    "8_report": {"phase": prepared.delivery_phase,
                                 "candidate_id": prepared.candidate_id[:12]},
                    "9_deliver": {"phase": submitted.delivery_phase,
                                  "commit": submitted.commit,
                                  "delivery_calls": len(delivery.calls)},
                    "10_finalize": {
                        "resolution": getattr(finalized.resolution, "result", None),
                        "report_path": finalized.report_path,
                    },
                }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
