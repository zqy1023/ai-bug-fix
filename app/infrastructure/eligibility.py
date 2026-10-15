"""任务资格的文件持久化。

资格键是 ``run_id + biztype + carrier``：同一轮执行里，一个运输商只自动修复
一个问题。任务按运输商串行处理，进程内锁只防止同一进程内的重复登记；
登记表落在本轮运行目录里，中断后恢复同一轮次时沿用这份记录，而不是重新领取。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
class ExecutionEligibility:
    """保证一次执行中同一运输商只自动修复一次。"""

    def __init__(self, root: str | Path | None = None) -> None:
        """创建或打开资格登记文件。"""
        self._claimed: dict[str, str] = {}
        self._lock = Lock()
        self._path = Path(root).resolve() / "eligibility.json" if root else None
        if self._path and self._path.is_file():
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._claimed = {str(key): str(value) for key, value in payload.items()}

    def claim(self, run_id: str, biztype: str, carrier: str, problem_key: str) -> bool:
        """领取本轮该运输商的唯一自动修复资格，首次领取返回 ``True``。

        键不含问题指纹：同一运输商本轮已经修过一个问题后，后续问题不再领取。
        登记值保留首次领取的问题指纹，便于中断后排查跳过原因。
        """
        key = f"{run_id}|{biztype}|{carrier}"
        with self._lock:
            if key in self._claimed:
                return False
            self._claimed[key] = problem_key
            self._persist()
            return True

    def claimed(self) -> dict[str, str]:
        """返回当前登记表的只读副本。"""
        with self._lock:
            return dict(self._claimed)

    def clear(self) -> None:
        """清空登记表，供测试夹具使用。"""
        with self._lock:
            self._claimed.clear()
            self._persist()

    def _persist(self) -> None:
        """把资格登记写入文件；内存模式只用于显式测试夹具。"""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._claimed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)
