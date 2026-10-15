"""Entry point shared by independently executable agents."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_project_root() -> None:
    """直接执行 ``__main__.py`` 时补上项目根，否则 ``config`` 不是包。"""
    root = Path(__file__).resolve().parents[2]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def run(agent_name: str) -> None:
    _ensure_project_root()
    from app.workflow.plugin_loader import load_agent
    from app.workflow.runner_cli import main_agent
    from app.workflow.spec import AgentSpec

    spec = load_agent(agent_name)
    if not isinstance(spec, AgentSpec):
        raise SystemExit(f"{agent_name} 不是 Agent")
    main_agent(spec)


__all__ = ["run"]
