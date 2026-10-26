"""防止正式工作流模型更新后，前端生成契约仍停留在旧版。"""
from pathlib import Path
import unittest

from app.workflow.frontend_contracts import generate


class FrontendContractTests(unittest.TestCase):
    def test_generated_contract_matches_backend(self):
        target = Path(__file__).resolve().parents[1] / "frontend/src/lib/workflow.generated.ts"
        self.assertEqual(target.read_text(encoding="utf-8"), generate())
