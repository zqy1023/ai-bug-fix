"""常驻会话版本与可恢复的增量上下文。"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable

from core.contracts import AgentRequest

_CONTEXT_LOCK = threading.Lock()


def session_fingerprint(
    request: AgentRequest,
    schema: dict[str, Any],
    config: dict[str, Any],
    environment: dict[str, str],
) -> str:
    """绑定初始化规则、模型、协议及运行配置，避免复用旧规则会话。"""
    value = [request.system_prompt, request.prompt_version, request.model, schema, config, environment]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def invoke_session(
    request: AgentRequest,
    session: Any,
    invoke: Callable[[AgentRequest], dict[str, Any]],
) -> dict[str, Any]:
    """仅在本会话成功收到完整前文后发送增量；会话重建时自然回到完整输入。"""
    with _CONTEXT_LOCK:
        lock = getattr(session, "_prompt_context_lock", None)
        if lock is None:
            lock = threading.RLock()
            session._prompt_context_lock = lock
    with lock:
        return _invoke_session(request, session, invoke)


def _invoke_session(
    request: AgentRequest,
    session: Any,
    invoke: Callable[[AgentRequest], dict[str, Any]],
) -> dict[str, Any]:
    """在独占租用中生成并提交本轮上下文。"""
    previous = getattr(session, "_task_prompt_context", None)
    current = request.prompt_context
    sent = request
    # 身份、当前差异及验证每轮重发；只省略未变化的背景字段。
    always = {
        "run_id", "task_id", "attempt", "framework", "carrier", "biztype", "service_name",
        "changed_files", "diff_summary",
        "allowed_read_roots", "allowed_write_roots", "allowed_scope", "budget", "retry_feedback",
    }
    if isinstance(previous, dict) and all(previous.get(k) == current.get(k) for k in ("run_id", "task_id")):
        update = {k: v for k, v in current.items() if k in always or k not in previous or previous[k] != v}
        delta = {
            "context_mode": "delta",
            "set": update,
            "removed": sorted(set(previous) - set(current)),
            "workspace_notice": "当前工作区可能恢复基线或被修改；旧源码和 diff 只作历史，当前文件以磁盘为准。",
        }
        prompt = "## 本轮上下文更新\n\n```json\n" + json.dumps(delta, ensure_ascii=False, separators=(",", ":")) + "\n```"
        if len(prompt) < len(request.prompt):
            digest = hashlib.sha256(f"{request.prompt_version}\0{request.system_prompt}\0{prompt}".encode()).hexdigest()
            sent = request.model_copy(update={
                "prompt": prompt,
                "prompt_hash": digest,
                "context_mode": "delta",
                "previous_context_hash": getattr(session, "_last_sent_prompt_hash", ""),
            })
    session._task_prompt_context = None
    result = invoke(sent)
    if result.get("reason") or not isinstance(result.get("result"), str):
        session._task_prompt_context = None
    else:
        session._task_prompt_context = copy.deepcopy(current)
        session._last_sent_prompt_hash = sent.prompt_hash
    return result


__all__ = ["session_fingerprint", "invoke_session"]
