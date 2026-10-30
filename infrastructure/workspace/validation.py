"""平台侧固定命令验证；模型仅能读取执行证据，不能选择命令。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from config.nacos import nacos_get
from core.policy import policy_of
from infrastructure.workspace.commands import run_fixed_commands
from infrastructure.workspace.workspace import WorkspaceManager
from infrastructure.workspace.diff import source_manifest
from infrastructure.workspace.runtime_environment import runtime_python, runtime_temporary_directory


def _validation_changes(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """仅放行框架日志器新建的精确命名日志；已有文件和其他新增文件仍核验。"""
    changed, runtime_files = [], []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        if name not in before and name in after and re.fullmatch(
            r"logs/gather\.py\.track_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.log(?:\.\d+)?", name
        ):
            runtime_files.append(name)
        else:
            changed.append(name)
    return changed, runtime_files


def _commands(value: Any) -> list[list[str]]:
    if not isinstance(value, list) or any(
        not isinstance(command, list) or not command
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
        for command in value
    ):
        raise ValueError("验证命令必须为非空 argv 数组列表")
    return value


def _receipt(result: dict[str, Any], *, baseline: bool) -> bool:
    """退出码不能代替业务断言；验证器须输出一行 JSON 执行回执。"""
    if not result.get("passed") or not result.get("results"):
        return False
    for item in result["results"]:
        try:
            output = json.loads(str(item.get("stdout") or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False
        if not isinstance(output, dict):
            return False
        if baseline:
            if output.get("reproduced") is not True:
                return False
        else:
            assertions = output.get("assertions")
            if output.get("passed") is not True or not isinstance(assertions, list) or not assertions:
                return False
            if any(not isinstance(check, dict) or not check.get("name") or check.get("passed") is not True
                   for check in assertions):
                return False
    return True


def run_validation(state: dict[str, Any]) -> dict[str, Any]:
    """在源快照副本中执行基线重放、补丁重放和真实请求，绑定原始输入。"""
    from workflow.engine.handoff import evidence_version
    policy = policy_of(state)
    problem = dict(state.get("problem") or {})
    workspace = str(state["ai_workspace_path"])
    baseline = str(state.get("baseline_dir") or "")
    files = list(state.get("changed_files") or [])
    version = evidence_version(workspace, files, baseline)
    result: dict[str, Any] = {
        "evidence_version": version,
        "input_id": hashlib.sha256(json.dumps(problem, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "replay": {"status": "unverified", "reason": "未配置固定重放命令"},
        "request": {"status": "unverified", "reason": "未配置真实请求验证命令"},
        "executions": [],
    }
    if not policy.acceptance:
        for kind in ("replay", "request"):
            result[kind]["reason"] = "执行策略关闭验收，未执行验证，不能视为通过"
        return result
    try:
        config = json.loads(nacos_get("AI_REPAIR_VALIDATION_COMMANDS", "{}"))
        if not isinstance(config, dict) or set(config) - {"replay", "request", "timeout_seconds"}:
            raise ValueError("验证配置只接受 replay、request、timeout_seconds")
        timeout = int(config.get("timeout_seconds", 60))
        if not 1 <= timeout <= 600:
            raise ValueError("验证超时必须位于 1～600 秒")
        for kind, enabled in (("replay", policy.reproduce and policy.verify_fixed),
                              ("request", policy.request_regression)):
            if not enabled:
                result[kind]["reason"] = "执行策略关闭此验证，不能视为通过"
                continue
            commands = _commands(config.get(kind, []))
            if not commands:
                source_root = Path(str(state.get("source_project_path") or ""))
                if not (source_root / "framework/track_server.py").is_file():
                    result[kind]["reason"] = "缺少可执行采集框架，无法执行内置验证"
                    continue
                commands = [[runtime_python(), str(Path(__file__).with_name("validation_runner.py")), "{input_path}"]]
            if any(not any("{input_path}" in argument for argument in command) for command in commands):
                raise ValueError("每条验证命令必须显式接收 {input_path}，不能脱离原始输入运行")
            if kind == "replay" and (not baseline or not problem.get("response")):
                result[kind]["reason"] = "缺少只读基线或原始录制 response"
                continue
            source = Path(str(state.get("source_project_path") or baseline or workspace)).resolve()
            # 每种验证、每个版本都使用独立临时目录，命令不能污染 AI 工作区和原始快照。
            verified = True
            phases = ("baseline", "patched") if kind == "replay" else ("patched",)
            for phase in phases:
                with runtime_temporary_directory(prefix="ai-repair-verify-") as temporary:
                    base = Path(temporary)
                    project = base / "project"
                    shutil.copytree(source, project, copy_function=shutil.copyfile, ignore=shutil.ignore_patterns(
                        ".git", "__pycache__", ".evidence", ".ai-bug-tasks", "node_modules"
                    ))
                    if baseline:
                        snapshot = Path(baseline)
                        baseline_files = [path.relative_to(snapshot).as_posix()
                                          for path in snapshot.rglob("*") if path.is_file()]
                        WorkspaceManager.copy_files(snapshot, project, baseline_files)
                    if phase == "patched":
                        WorkspaceManager.copy_files(workspace, project, files)
                    input_path = base / "input.json"
                    input_path.write_text(json.dumps({"problem": problem, "phase": phase, "kind": kind,
                        "evidence_version": version, "input_id": result["input_id"]}, ensure_ascii=False), encoding="utf-8")
                    tokens = {"{workspace}": str(project), "{input_path}": str(input_path),
                              "{phase}": phase, "{carrier}": str(state.get("carrier") or "")}
                    argv = []
                    for command in commands:
                        expanded = []
                        for argument in command:
                            for token, value in tokens.items():
                                argument = argument.replace(token, value)
                            expanded.append(argument)
                        argv.append(expanded)
                    source_before = source_manifest(project)
                    executed = run_fixed_commands(project, argv, timeout_seconds=timeout)
                    mutations, runtime_files = _validation_changes(source_before, source_manifest(project))
                    executed.update(source_mutations=mutations, runtime_artifacts=runtime_files)
                    if mutations:
                        executed.update(passed=False, reason="验证命令修改了被验证源码，执行证据无效")
                    result["executions"].append({"kind": kind, "phase": phase, **executed})
                    verified = verified and _receipt(executed, baseline=phase == "baseline")
            reasons = []
            for execution in result["executions"]:
                if execution["kind"] != kind:
                    continue
                if execution.get("source_mutations"):
                    reasons.append(execution["phase"] + ": " + execution["reason"])
                for item in execution.get("results") or []:
                    try:
                        receipt = json.loads(item.get("stdout", "").strip().splitlines()[-1])
                        if receipt.get("reason"):
                            reasons.append(execution["phase"] + ": " + receipt["reason"])
                    except (ValueError, IndexError):
                        reasons.append(execution["phase"] + ": 验证进程未返回有效回执")
            result[kind] = {"status": "passed" if verified else "unverified",
                            "reason": "固定命令执行及业务断言通过" if verified else "；".join(reasons) or "执行失败或缺少有效业务断言；需核对原始执行证据"}
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        result["error"] = f"受控验证未完成: {type(error).__name__}"
    return result


def validation_summary(report: dict[str, Any]) -> dict[str, Any]:
    """交付与提示词保留状态和引用，命令原文及原始输出仅进入内部产物。"""
    return {key: value for key, value in report.items() if key != "executions"}
