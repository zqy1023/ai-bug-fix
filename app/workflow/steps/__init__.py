"""一个步骤一个目录；目录名、插件名、记录名和调试模块名保持一致。

每步由 ``models.py`` 定义契约，``node.py`` 实现单一职责，``__main__.py``
从真实运行记录重建输入并独立执行。
"""
from __future__ import annotations

import importlib
import sys

_SUBMODULE_MAP = {
    "claim_repair_slot": "access.claim_repair_slot",
    "request_approval": "approval.request_approval",
    "define_acceptance": "context.define_acceptance",
    "prepare_analysis": "context.prepare_analysis",
    "prepare_evidence": "context.prepare_evidence",
    "prepare_source_snapshot": "context.prepare_source_snapshot",
    "prepare_workspace": "context.prepare_workspace",
    "reproduce_baseline": "context.reproduce_baseline",
    "prepare_delivery": "delivery.prepare_delivery",
    "publish_delivery": "delivery.publish_delivery",
    "repair_branch_preflight": "delivery.repair_branch_preflight",
    "finalize_execution": "report.finalize_execution",
    "discover_logs": "run.discover_logs",
    "normalize_error": "run.normalize_error",
    "deduplicate_error": "run.deduplicate_error",
    "identify_carrier": "run.identify_carrier",
}

for _step_name, _category_path in _SUBMODULE_MAP.items():
    _full_old = f"app.workflow.steps.{_step_name}"
    _full_new = f"app.workflow.steps.{_category_path}"
    try:
        _mod = importlib.import_module(_full_new)
        sys.modules[_full_old] = _mod
        globals()[_step_name] = _mod
        for _sub in ("models", "node", "report_paths", "normalizer", "approval"):
            try:
                _submod = importlib.import_module(f"{_full_new}.{_sub}")
                sys.modules[f"{_full_old}.{_sub}"] = _submod
                setattr(_mod, _sub, _submod)
            except ImportError:
                pass
    except ImportError:
        pass
