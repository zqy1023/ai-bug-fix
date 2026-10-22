"""Compatibility shim: legacy repair bundles are no longer repository deliverables."""
from pathlib import Path
from typing import Any, Mapping


def write_repair_bundle(project: Path, state: Mapping[str, Any]) -> list[str]:
    """Keep legacy callers compatible without generating obsolete attachments."""
    return []
