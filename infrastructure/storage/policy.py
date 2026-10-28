"""运行产物保留策略；精简模式仍保留业务结论与恢复数据。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)


def artifact_mode(run_dir: Path | None = None) -> str:
    """返回 compact 或 debug；配置错误明确失败，避免悄悄丢失调试产物。"""
    from config.nacos import nacos_get
    mode = nacos_get("AI_REPAIR_ARTIFACT_MODE", "compact").strip().lower()
    if run_dir is not None and (run_dir / "run.json").is_file():
        try:
            payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
            # run.json 损坏不能让保留策略计算失败、进而阻断整个运行；
            # 按默认策略继续，同时留下可查的告警。
            logger.warning(
                "run.json 损坏，按默认产物保留策略继续: %s (%s)",
                run_dir / "run.json",
                type(error).__name__,
            )
        else:
            if isinstance(payload, dict):
                mode = str(payload.get("artifact_mode") or mode)
    if mode not in {"compact", "debug"}:
        raise ValueError("AI_REPAIR_ARTIFACT_MODE 必须是 compact 或 debug")
    return mode


def debug_artifacts(run_dir: Path | None = None) -> bool:
    """是否保留额外调试产物及原始模型结果；调用审计独立保留。"""
    return artifact_mode(run_dir) == "debug"
