"""权威执行阶段定义 (7 大业务阶段与批次、收尾对应)。

通过预映射机制将数字前缀物理目录注册为标准别名模块：
- 00_log_pipeline      -> log_pipeline
- 01_prepare_context   -> prepare_context
- 02_claim_slot        -> claim_slot
- 03_unified_fixer     -> unified_fixer
- 04_review            -> review
- 05_generate_report   -> generate_report
- 06_submit_changes    -> submit_changes
- 07_request_approval  -> request_approval
- 99_finalize          -> finalize
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from typing import Any

# 别名映射到真实物理数字前缀目录
_STAGE_DIR_MAP: dict[str, str] = {
    "workflow.stages.log_pipeline": "workflow.stages.00_log_pipeline",
    "workflow.stages.prepare_context": "workflow.stages.01_prepare_context",
    "workflow.stages.claim_slot": "workflow.stages.02_claim_slot",
    "workflow.stages.unified_fixer": "workflow.stages.03_unified_fixer",
    "workflow.stages.review": "workflow.stages.04_review",
    "workflow.stages.generate_report": "workflow.stages.05_generate_report",
    "workflow.stages.submit_changes": "workflow.stages.06_submit_changes",
    "workflow.stages.request_approval": "workflow.stages.07_request_approval",
    "workflow.stages.finalize": "workflow.stages.99_finalize",
}

# 注册所有顶级阶段模块
for alias, actual in _STAGE_DIR_MAP.items():
    try:
        actual_mod = importlib.import_module(actual)
        sys.modules[alias] = actual_mod
        short_name = alias.split(".")[-1]
        globals()[short_name] = actual_mod
    except Exception:
        pass

# 注册所有子模块映射
for alias, actual in _STAGE_DIR_MAP.items():
    if actual in sys.modules:
        actual_mod = sys.modules[actual]
        if hasattr(actual_mod, "__path__"):
            try:
                for info in pkgutil.walk_packages(actual_mod.__path__, prefix=f"{actual}."):
                    try:
                        sub_mod = importlib.import_module(info.name)
                        sub_alias = info.name.replace(actual, alias)
                        sys.modules[sub_alias] = sub_mod
                        setattr(sys.modules[alias], info.name.split(".")[-1], sub_mod)
                    except Exception:
                        pass
            except Exception:
                pass


def __getattr__(name: str) -> Any:
    full_alias = f"workflow.stages.{name}"
    if full_alias in sys.modules:
        return sys.modules[full_alias]
    if full_alias in _STAGE_DIR_MAP:
        actual = _STAGE_DIR_MAP[full_alias]
        mod = importlib.import_module(actual)
        sys.modules[full_alias] = mod
        return mod
    for actual_dir in [
        "00_log_pipeline", "01_prepare_context", "02_claim_slot",
        "03_unified_fixer", "04_review", "05_generate_report",
        "06_submit_changes", "07_request_approval", "99_finalize"
    ]:
        if name == actual_dir:
            return importlib.import_module(f"workflow.stages.{actual_dir}")
    raise AttributeError(f"module 'workflow.stages' has no attribute '{name}'")


__all__ = [
    "log_pipeline",
    "prepare_context",
    "claim_slot",
    "unified_fixer",
    "review",
    "generate_report",
    "submit_changes",
    "request_approval",
    "finalize",
]
