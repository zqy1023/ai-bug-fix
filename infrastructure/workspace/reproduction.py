"""日志与源码初步分析后仍无明确原因时，按原始请求补充复现证据。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from core.policy import policy_of
from infrastructure.workspace.commands import run_fixed_commands
from infrastructure.workspace.workspace import WorkspaceManager
from infrastructure.workspace.runtime_environment import runtime_python, runtime_temporary_directory
from infrastructure.workspace.request_input import original_request


def needs_reproduction(problem: dict[str, Any]) -> bool:
    """仅提示日志是否模糊；不能据此绕过模型分析或直接跳过任务。"""
    text = str(problem.get("original_exception_message") or problem.get("message") or "")
    if problem.get("frame") or problem.get("exception_class") or "Traceback (most recent call last)" in text:
        return False
    if not text:
        return True
    if re.search(r"\b[A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)\s*:", text):
        return False
    detail = re.search(r"msg\s*:\s*(.*)$", text)
    if detail and detail.group(1).strip().casefold() not in {"", "none", "null"}:
        # 响应 JSON 不能被误认为明确的异常原因。
        if not detail.group(1).lstrip().startswith(("{", "[")):
            return False
    empty_detail = bool(re.search(r"msg\s*:\s*(?:None|null)?\s*$", text))
    return empty_detail or not problem.get("response")


def reproduce_before_analysis(state: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "unavailable", "reproduced": False,
                              "reason": "请求复现未完成，不能确认原异常"}
    try:
        request = original_request(dict(state.get("problem") or {}))
        report["request"] = request
        report["input_id"] = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if not policy_of(state).reproduce:
            report["detail"] = "策略关闭请求复现"
            return report
        if not request.get("carrier") or not isinstance(request.get("requests"), list) or not request["requests"]:
            report.update(status="insufficient_input", reason="已有分析证据不足，且缺少原始单号或 carrier，无法补充请求证据")
            raise ValueError("缺少原始单号或 carrier")
        if any(not isinstance(item, dict) or not item.get("number") for item in request["requests"]):
            report.update(status="insufficient_input", reason="已有分析证据不足，且请求项缺少原始单号，无法补充请求证据")
            raise ValueError("请求项缺少原始单号")
        source = Path(str(state.get("source_project_path") or ""))
        if not state.get("source_project_path") or not (source / "framework/track_server.py").is_file():
            raise ValueError("缺少可执行采集框架")
        with runtime_temporary_directory(prefix="ai-request-repro-") as temporary:
            base = Path(temporary)
            project = base / "project"
            # 快照可能带只读属性；副本必须可覆盖原始 carrier 基线，不继承文件属性。
            shutil.copytree(source, project, copy_function=shutil.copyfile, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "node_modules", ".evidence", ".ai-bug-tasks"))
            baseline = str(state.get("source_snapshot", {}).get("baseline_dir") or state.get("baseline_dir") or "")
            if baseline:
                snapshot = Path(baseline)
                WorkspaceManager.copy_files(snapshot, project, [p.relative_to(snapshot).as_posix()
                                                               for p in snapshot.rglob("*") if p.is_file()])
            input_path = base / "request.json"
            input_path.write_text(json.dumps({"request": request}, ensure_ascii=False), encoding="utf-8")
            command = [runtime_python(), str(Path(__file__).with_name("reproduction_runner.py")), str(input_path)]
            executed = run_fixed_commands(project, [command], timeout_seconds=90)
            report["execution"] = executed
            if not executed.get("passed"):
                report["detail"] = executed.get("reason") or "请求执行失败"
                return report
            receipt = json.loads(executed["results"][-1]["stdout"].strip().splitlines()[-1])
            exceptions = receipt.get("exceptions") or []
            if receipt.get("reproduced") is True and receipt.get("request") == request and exceptions and all(
                isinstance(item, dict) and item.get("traceback") and item.get("exception_class") for item in exceptions
            ):
                report.update(status="reproduced", reproduced=True, reason="已通过原始请求复现异常并捕获业务堆栈",
                              exceptions=exceptions, response=receipt.get("response"))
            else:
                report.update(status="unavailable" if receipt.get("execution_error") else "not_reproduced",
                              detail=receipt.get("reason") or "没有有效异常堆栈")
                if not receipt.get("execution_error"):
                    report["reason"] = "已有分析证据不足，原始请求未复现带业务堆栈的解析异常，跳过修复"
                if receipt.get("execution_error"):
                    report["execution_error"] = receipt["execution_error"]
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        report["detail"] = f"请求复现无法完成: {type(error).__name__}: {error}"
    return report
