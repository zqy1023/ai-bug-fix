"""逐调用预算与拒绝收敛；拒绝不冒充已执行工具。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ToolBudget:
    """放行量受预算限制，连续拒绝停止无效循环。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.attempts = 0
        self.allowed = 0
        self.denied = 0
        self.consecutive_denials = 0
        self.signatures: dict[str, int] = {}
        self.stop_reason = ""
        self.stop_kind = ""

    def decide(self, name: str, values: dict[str, Any], decision: str, reason: str) -> tuple[str, str]:
        self.attempts += 1
        signature_data: list[Any] = [name, values]
        if name == "Read":
            try:
                stat = Path(str(values.get("file_path") or "")).stat()
                signature_data.append([stat.st_mtime_ns, stat.st_size])
            except OSError:
                pass
        signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, default=str).encode()).hexdigest()
        if decision == "allow":
            self.signatures[signature] = self.signatures.get(signature, 0) + 1
            if self.allowed >= self.limit:
                decision, reason = "deny", f"工具放行预算 {self.limit} 已耗尽；停止工具调用并输出当前结论与缺口"
                self.stop_kind = "tool_budget_exhausted"
            elif self.signatures[signature] > 2:
                decision, reason = "deny", "相同工具参数及文件版本已读取两次；请使用已有证据，停止重复探索"
            elif name == "Edit" and "old_string" in values and values.get("old_string") == values.get("new_string"):
                decision, reason = "deny", "Edit 的新旧内容相同，没有实际修改；请核对当前文件后输出结论"
        if decision == "allow":
            self.allowed += 1
            self.consecutive_denials = 0
        else:
            self.denied += 1
            self.consecutive_denials += 1
            if self.stop_kind == "tool_budget_exhausted" or self.consecutive_denials >= 3:
                self.stop_kind = self.stop_kind or "tool_access_blocked"
                self.stop_reason = reason
        return decision, reason

    def hook_output(self, name: str, values: dict[str, Any], decision: str, reason: str) -> dict[str, Any]:
        specific = {"hookEventName": "PreToolUse", "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                    "additionalContext": f"工具放行 {self.allowed}/{self.limit}，拒绝 {self.denied}；"
                                         "优先处理当前异常，不再扫描仓库。剩余两次时应完成修改并输出结论。"}
        if decision == "allow":
            specific["updatedInput"] = values
        output: dict[str, Any] = {"hookSpecificOutput": specific}
        if self.stop_reason:
            output.update({"continue_": False, "stopReason": self.stop_reason})
        return output

    def usage(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        results = [e for e in events if e.get("type") == "tool_result"]
        file_uses = [e for e in events if e.get("type") == "tool_use" and e.get("tool") != "StructuredOutput"]
        completed = {e.get("tool_use_id") for e in results if e.get("tool_use_id")}
        denied = {e.get("tool_use_id") for e in events if e.get("type") == "tool_decision" and e.get("decision") == "deny"}
        return {"tool_calls": self.attempts, "tool_attempts": self.attempts, "tool_allowed": self.allowed,
                "tool_denied": self.denied, "tool_completed": sum(bool(e.get("tool_use_id")) and e.get("tool_use_id") in completed and e.get("tool_use_id") not in denied for e in file_uses),
                "tool_errors": sum(bool(e.get("is_error")) and e.get("tool_use_id") not in denied for e in results),
                "structured_output_calls": sum(e.get("type") == "tool_use" and e.get("tool") == "StructuredOutput" for e in events),
                "tool_stop_reason": self.stop_reason, "tool_stop_kind": self.stop_kind}
