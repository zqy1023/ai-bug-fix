"""Prompt 规则片段选择与预算分级回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.contracts import AgentRequest
from infrastructure.llm.common import audit_metadata
from infrastructure.llm.loader import PromptLoader
from infrastructure.llm.renderer import PromptRenderer
from infrastructure.llm.models import PromptContext
from infrastructure.llm.rules import (
    ALL_RULES,
    PROFILE_ENV,
    budget_tier,
    select_rules,
)


def _rules(**kwargs):
    base = {"role": "unified_fixer", "framework": "gathers", "problem": {}, "evidence": {}}
    base.update(kwargs)
    with patch.dict(os.environ, {PROFILE_ENV: "slim"}):
        return select_rules(**base)


class SelectRulesTests(unittest.TestCase):
    def test_undetermined_injects_all_domain_rules(self):
        """判不出归属时全量注入，宁可多花 token 也不漏业务规则。"""
        with patch.dict(os.environ, {PROFILE_ENV: "slim"}):
            selected = select_rules(
                role="unified_fixer", framework="gathers",
                problem={"blame": "undetermined"}, evidence={},
            )
        self.assertEqual(selected, ALL_RULES)

    def test_code_blame_keeps_coding_plus_matched_domains(self):
        selected = _rules(problem={
            "blame": "code",
            "exception_class": "builtins.IndexError",
            "error": "builtins.IndexError, list index out of range",
        })
        self.assertIn("coding", selected)
        self.assertIn("exception", selected)
        self.assertNotIn("legacy", selected)

    def test_block_marker_pulls_block_rules(self):
        selected = _rules(problem={
            "blame": "upstream", "response_kind": "html",
            "error": "Expecting value",
        })
        self.assertIn("block", selected)

    def test_legacy_framework_pulls_legacy_rules(self):
        selected = _rules(framework="spiders", problem={"blame": "code", "error": "boom"})
        self.assertIn("legacy", selected)

    def test_timezone_hint_pulls_timezone_rules(self):
        selected = _rules(problem={
            "blame": "code", "error": "未识别的时间格式, key:track_result_events.datetime",
        })
        self.assertIn("timezone", selected)

    def test_full_profile_disables_slimming(self):
        with patch.dict(os.environ, {PROFILE_ENV: "full"}):
            self.assertIsNone(select_rules(
                role="unified_fixer", framework="gathers",
                problem={"blame": "code"}, evidence={},
            ))

    def test_review_does_not_duplicate_coding_rules(self):
        selected = _rules(role="review", problem={"blame": "code", "error": "boom"})
        self.assertNotIn("coding", selected)

    def test_notfound_rules_are_mandatory_even_for_no_events_error(self):
        for role in ("unified_fixer", "review"):
            selected = _rules(role=role, problem={"blame": "code", "message_format": "model",
                                                 "error": "ProcessError: no events"})
            self.assertIn("notfound", selected)


class BudgetTierTests(unittest.TestCase):
    def test_small_when_evidence_is_complete(self):
        tier = budget_tier(blame="code", frame="a.py:1:f", has_response=True, candidate_count=1)
        self.assertEqual(tier.name, "small")
        self.assertEqual((tier.max_turns, tier.max_tool_calls, tier.max_context_chars), (16, 24, 30000))

    def test_default_without_response(self):
        tier = budget_tier(blame="code", frame="a.py:1:f", has_response=False, candidate_count=3)
        self.assertEqual(tier.name, "default")

    def test_complex_without_frame(self):
        tier = budget_tier(blame="undetermined", frame="", has_response=False, candidate_count=5)
        self.assertEqual(tier.name, "complex")
        self.assertEqual(tier.max_turns, 32)

    def test_field_clue_downgrades_to_default(self):
        """有 key: 字段路径（无栈帧）时不必再按 complex 探索。"""
        tier = budget_tier(
            blame="undetermined", frame="", has_response=False,
            candidate_count=3, field="track_result_events.datetime",
        )
        self.assertEqual(tier.name, "default")


class PromptRenderingTests(unittest.TestCase):
    def test_slim_rendering_is_much_smaller(self):
        loader = PromptLoader()
        full, _ = loader.load("unified_fixer")
        slim, _ = loader.load("unified_fixer", ("coding",))
        self.assertIn("core/2.0.0", slim.version)
        self.assertIn("coding/2.0.0", slim.version)
        # 核心 + 编码规则应显著小于全量 9 个章节。
        self.assertLess(len(slim.body), len(full.body) * 0.6)

    def test_small_role_variant_is_shorter(self):
        loader = PromptLoader()
        _, full_role = loader.load("unified_fixer", ("coding",))
        _, small_role = loader.load("unified_fixer", ("coding",), "small")
        self.assertIn("精简档", small_role.body)
        self.assertLess(len(small_role.body), len(full_role.body) * 0.85)
        # 输出契约必须完整保留，否则模型无法守协议。
        self.assertIn('"attribution_type"', small_role.body)
        self.assertIn('"change_plan"', small_role.body)

    def test_full_and_slim_share_current_role_boundaries(self):
        loader = PromptLoader()
        for role in ("unified_fixer", "review"):
            full, role_prompt = loader.load(role)
            slim, same_role = loader.load(role, ("notfound",))
            self.assertIn("core/2.0.0", full.version)
            self.assertIn("core/2.0.0", slim.version)
            self.assertEqual(role_prompt, same_role)
            self.assertNotIn("Locate", full.body)
            self.assertNotIn("三个 Agent", full.body)
            self.assertNotIn("server_dev.main", role_prompt.body)
        _, reviewer = loader.load("review", ("notfound",))
        self.assertIn("原异常是否得到正确处理", reviewer.body)
        self.assertIn("是否影响其他业务", reviewer.body)
        self.assertIn("不能先给 PASS", reviewer.body)

    def test_read_scope_is_structured(self):
        context = PromptContext(
            agent_role="unified_fixer", run_id="run", task_id="task", attempt=1,
            service_name="gather.py.track", biztype="b", carrier="TEST",
            framework="gathers", problem={}, source_snapshot={}, evidence={},
            changed_files=(), diff_summary="", allowed_scope=(), stop_conditions=(),
            allowed_read_roots=(), allowed_write_roots=(),
            budget={"max_turns": 6},
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "ai-task"
            shared = Path(directory) / "shared" / "cache-abc"
            (workspace / "gathers").mkdir(parents=True)
            (shared / "framework").mkdir(parents=True)
            (shared / "common").mkdir(parents=True)
            context = context.model_copy(update={"allowed_read_roots": (
                str(workspace / "gathers" / "TEST"),
                str(shared / "framework"),
                str(shared / "common"),
            )})
            from infrastructure.llm.context import display_context
            display = display_context(context, str(workspace))
        scope = display.read_scope
        self.assertEqual(scope["carrier"], ["gathers/TEST"])
        self.assertEqual(scope["shared_root"], shared.as_posix())
        self.assertEqual(sorted(scope["shared_paths"]), ["common", "framework"])

    def test_renderer_hides_raw_roots_but_request_keeps_them(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "ai-task"
            (workspace / "gathers" / "TEST").mkdir(parents=True)
            context = PromptContext(
                agent_role="unified_fixer", run_id="run", task_id="task", attempt=1,
                service_name="gather.py.track", biztype="b", carrier="TEST",
                framework="gathers", problem={}, source_snapshot={}, evidence={},
                changed_files=(), diff_summary="", allowed_scope=(), stop_conditions=(),
                allowed_read_roots=(str(workspace / "gathers" / "TEST"),),
                allowed_write_roots=(str(workspace / "gathers" / "TEST"),),
                budget={"max_turns": 6},
            )
            request = PromptRenderer().request(
                "unified_fixer", context, stage="unified_fixer",
                workspace=str(workspace), model="m", rule_ids=("coding",),
            )
        self.assertNotIn("allowed_read_roots", request.prompt)
        self.assertIn("read_scope", request.prompt)
        # 请求契约仍保留真实绝对路径（bounded_context 会再补一个上下文证据目录），
        # 权限校验依赖它们。
        self.assertGreaterEqual(len(request.allowed_read_roots), 1)
        self.assertTrue(all(Path(item).is_absolute() for item in request.allowed_read_roots))

    def test_unknown_rule_id_fails_loud(self):
        with self.assertRaises(Exception):
            PromptLoader().load("unified_fixer", ("not_a_rule",))

    def test_request_records_rule_ids_and_tier(self):
        context = PromptContext(
            agent_role="unified_fixer", run_id="run", task_id="task", attempt=1,
            service_name="gather.py.track", biztype="framework.track_server", carrier="TEST",
            framework="gathers", problem={}, source_snapshot={}, evidence={},
            changed_files=(), diff_summary="", allowed_scope=(), stop_conditions=(),
            allowed_read_roots=(), allowed_write_roots=(),
            budget={"max_turns": 6, "max_tool_calls": 8, "max_context_chars": 30000},
        )
        with tempfile.TemporaryDirectory() as directory:
            request = PromptRenderer().request(
                "unified_fixer", context, stage="unified_fixer",
                workspace=directory, model="test-model",
                rule_ids=("coding", "exception"), budget_tier="small",
            )
        self.assertIsInstance(request, AgentRequest)
        self.assertEqual(request.rule_ids, ("coding", "exception"))
        self.assertEqual(request.budget_tier, "small")
        self.assertIn("core/2.0.0", request.prompt_version)
        metadata = audit_metadata(request, {})
        self.assertEqual(metadata["rule_ids"], ["coding", "exception"])
        self.assertEqual(metadata["budget_tier"], "small")


if __name__ == "__main__":
    unittest.main()
