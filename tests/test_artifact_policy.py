"""运行级产物保留策略与损坏元数据回退。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PolicyArtifactModeTests(unittest.TestCase):
    """run.json 损坏时按默认策略继续并告警。"""

    def test_corrupted_run_json_falls_back_to_env_mode(self):
        from app.infrastructure.records.policy import artifact_mode

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run.json").write_text("{not json", encoding="utf-8")
            with patch.dict(
                os.environ, {**os.environ, "AI_REPAIR_ARTIFACT_MODE": "debug"}
            ):
                self.assertEqual(artifact_mode(run_dir), "debug")

    def test_valid_run_json_still_wins(self):
        import json

        from app.infrastructure.records.policy import artifact_mode

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run.json").write_text(
                json.dumps({"artifact_mode": "compact"}), encoding="utf-8"
            )
            with patch.dict(
                os.environ, {**os.environ, "AI_REPAIR_ARTIFACT_MODE": "debug"}
            ):
                self.assertEqual(artifact_mode(run_dir), "compact")
