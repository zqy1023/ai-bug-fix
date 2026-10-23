"""运行产物保留策略；精简模式仍保留业务结论与恢复数据。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.settings import settings


def artifact_mode(run_dir: Path | None = None) -> str:
    """返回 compact 或 debug；配置错误明确失败，避免悄悄丢失调试产物。"""
    mode = os.getenv("AI_REPAIR_ARTIFACT_MODE", settings.artifact_mode).strip().lower()
    if run_dir is not None and (run_dir / "run.json").is_file():
        payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        mode = str(payload.get("artifact_mode") or mode)
    if mode not in {"compact", "debug"}:
        raise ValueError("AI_REPAIR_ARTIFACT_MODE 必须是 compact 或 debug")
    return mode


def debug_artifacts(run_dir: Path | None = None) -> bool:
    """是否持久化完整提示词、公开事件和原始模型结果。"""
    return artifact_mode(run_dir) == "debug"
