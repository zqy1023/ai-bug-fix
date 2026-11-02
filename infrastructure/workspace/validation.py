"""平台侧固定命令验证；模型仅能读取执行证据，不能选择命令。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from config.nacos import nacos_get
from core.policy import policy_of
from infrastructure.workspace.commands import run_fixed_commands
from infrastructure.workspace.diff import source_manifest
from infrastructure.workspace.runtime_environment import (
    runtime_python,
    runtime_temporary_directory,
)
from infrastructure.workspace.workspace import WorkspaceManager


def _validation_changes(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """仅放行框架日志器新建的运行时日志；已有文件和其他新增文件仍核验。"""
    from infrastructure.workspace.runtime_artifacts import classify_changes

    return classify_changes(before, after)


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
        if output.get("outcome") == "environment_unavailable":
            # 验证根本没跑起来，不能算"断言未通过"。
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


def _environment_unavailable(result: dict[str, Any]) -> bool:
    """本轮验证是否因环境未就绪而根本没跑到被测代码。"""
    for item in result.get("results") or []:
        try:
            output = json.loads(str(item.get("stdout") or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            continue
        if isinstance(output, dict) and output.get("outcome") == "environment_unavailable":
            return True
    return False


_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".evidence", ".ai-bug-tasks", "node_modules",
    # 本地开发模式下源码树里常带这些，复制它们既慢又无用：验证用的是 runtime_python。
    ".venv", ".runtime-venv", ".idea", ".pytest_cache", ".ruff_cache",
)


def _copy_source(source: Path, base: Path) -> Path:
    """把一个独立的源码树副本放进临时目录（每种验证只做一次）。"""
    project = base / "project"
    shutil.copytree(source, project, copy_function=shutil.copyfile, ignore=_COPY_IGNORE)
    return project


def _apply_phase(
    project: Path,
    baseline: Path | None,
    workspace: Path,
    files: list[str],
    phase: str,
) -> None:
    """把某个阶段的源码内容叠加到副本上。

    - ``baseline``：先用只读基线覆盖全部被审查文件（回到改动前）。
    - ``patched``：再用工作区版本覆盖 ``files``（等同改动后）。

    两个阶段复用同一个副本，避免重复复制全仓（95 MiB / 1.27 万文件单次约 60 秒，
    replay 的 baseline+patched 加 request 共三次）。副本内部仍是"先还原基线、
    再按需打补丁"，逻辑与每次独立复制等价。
    """
    if baseline is not None and baseline.is_dir():
        snapshot_files = [path.relative_to(baseline).as_posix()
                          for path in baseline.rglob("*") if path.is_file()]
        WorkspaceManager.copy_files(baseline, project, snapshot_files)
    if phase == "patched" and files:
        WorkspaceManager.copy_files(workspace, project, files)


def _clear_runtime_artifacts(project: Path, names: list[str]) -> None:
    """删除上一阶段框架新建的运行时日志，让下一阶段重新从"无日志"开始。

    共用副本后，baseline 阶段写的日志会残留到 patched 阶段，第二条执行就不再
    是"框架新建日志"，运行时产物证据会凭空消失。这里显式清理，恢复每个阶段
    独立的语义；清理掉的路径已记入上一阶段的 ``runtime_artifacts``，信息不丢。
    """
    for name in names:
        target = (project / name).resolve()
        if target.is_file() and target.is_relative_to(project.resolve()):
            try:
                target.unlink()
            except OSError:
                pass


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
                    # 环境未就绪：没有可执行的采集框架，本轮根本没跑到被测代码。
                    # 必须与"业务断言未通过"分开，否则正确的修复会被误判为失败。
                    result[kind] = {
                        "status": "unverified",
                        "outcome": "environment_unavailable",
                        "reason": "缺少可执行采集框架，无法执行内置验证",
                        "next_action": "确认 source_project_path 指向含 framework/track_server.py 的采集源码树后重跑验证",
                    }
                    continue
                commands = [[runtime_python(), str(Path(__file__).with_name("validation_runner.py")), "{input_path}"]]
            if any(not any("{input_path}" in argument for argument in command) for command in commands):
                raise ValueError("每条验证命令必须显式接收 {input_path}，不能脱离原始输入运行")
            if kind == "replay" and (not baseline or not problem.get("response")):
                result[kind]["reason"] = "缺少只读基线或原始录制 response"
                continue
            source = Path(str(state.get("source_project_path") or baseline or workspace)).resolve()
            baseline_path = Path(baseline) if baseline else None
            verified = True
            phases = ("baseline", "patched") if kind == "replay" else ("patched",)
            # 同一个 kind 的多个 phase 共用一个临时目录，源码树只复制一次：
            # baseline 阶段用只读基线覆盖被审查文件，patched 阶段再覆盖为工作区版本。
            # 此前每个 phase 各复制一次全仓（95 MiB / 1.27 万文件单次约 60 秒），
            # replay 的 baseline+patched 加 request 共三次，时间几乎全花在复制上。
            # 隔离仍在 kind 之间保留：replay 与 request 各自独立目录，
            # 一个验证的副作用不会带进另一个。
            with runtime_temporary_directory(prefix="ai-repair-verify-") as temporary:
                base = Path(temporary)
                project = _copy_source(source, base)
                for phase in phases:
                    _apply_phase(project, baseline_path, Path(workspace), files, phase)
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
                    # 共用副本：把本阶段新建的运行日志清掉，下一阶段才是干净的起点。
                    _clear_runtime_artifacts(project, runtime_files)
            reasons = []
            environment_blocked = False
            for execution in result["executions"]:
                if execution["kind"] != kind:
                    continue
                if execution.get("source_mutations"):
                    reasons.append(execution["phase"] + ": " + execution["reason"])
                for item in execution.get("results") or []:
                    try:
                        receipt = json.loads(item.get("stdout", "").strip().splitlines()[-1])
                    except (ValueError, IndexError):
                        reasons.append(execution["phase"] + ": 验证进程未返回有效回执")
                        continue
                    if receipt.get("outcome") == "environment_unavailable":
                        environment_blocked = True
                    if receipt.get("reason"):
                        reasons.append(execution["phase"] + ": " + receipt["reason"])
            # 「环境未就绪」和「断言未通过」必须分开呈现：前者说明验证根本没跑到
            # 被测代码，业务正确性无从谈起；混写成"业务断言未通过"会让正确的修复
            # 被误判为失败（真实批次里 13/14 个任务因此显示成 unverified）。
            if environment_blocked:
                result[kind] = {
                    "status": "unverified",
                    "outcome": "environment_unavailable",
                    "reason": "；".join(reasons) or "采集验证环境未就绪，本轮未执行到被测代码",
                    "next_action": "恢复采集运行环境（框架日志目录、nacos 快照、目标 carrier 的采集实现）后重跑验证；"
                                   "当前结果不能用于判断修复是否正确",
                }
            else:
                result[kind] = {"status": "passed" if verified else "unverified",
                                "reason": "固定命令执行及业务断言通过" if verified else "；".join(reasons) or "执行失败或缺少有效业务断言；需核对原始执行证据"}
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        result["error"] = f"受控验证未完成: {type(error).__name__}"
    return result


def validation_summary(report: dict[str, Any]) -> dict[str, Any]:
    """交付与提示词保留状态和引用，命令原文及原始输出仅进入内部产物。"""
    return {key: value for key, value in report.items() if key != "executions"}
