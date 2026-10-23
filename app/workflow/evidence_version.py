"""将确定性证据绑定到当前源码内容，避免用 attempt 猜测补丁版本。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def evidence_version(workspace: str, files: list[str], baseline_dir: str = "") -> dict[str, Any]:
    """对本轮文件及其基线计算内容身份；删除保留显式标记。"""
    def digest(root: str) -> str:
        base = Path(root).resolve()
        values: dict[str, str] = {}
        for name in sorted(set(files)):
            target = (base / name).resolve()
            if not target.is_relative_to(base):
                raise ValueError("证据文件越出工作区")
            values[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "deleted"
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    return {"patch_id": digest(workspace), "baseline_id": digest(baseline_dir) if baseline_dir else "",
            "identity_version": 1}
