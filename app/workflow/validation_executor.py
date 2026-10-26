"""隔离的正文重放执行器，与图装配无关。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceManager
from config.settings import settings

# 重放工作区复制上限：单次复制超过该体积直接失败，避免写满宿主磁盘。
_COPY_LIMIT_BYTES = 2 * 1024**3


def _copy_limit_failure(source: Path, label: str) -> dict[str, Any] | None:
    """复制前累计源目录文件大小；超过上限时返回本执行器统一风格的失败。"""
    import os

    total = 0
    for current, directories, filenames in os.walk(source):
        # 复制时忽略 .git，统计口径保持一致。
        directories[:] = [name for name in directories if name != ".git"]
        for name in filenames:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
        if total > _COPY_LIMIT_BYTES:
            return {
                "passed": False,
                "status": "unverified",
                "mode": "response_replay",
                "reason": f"{label}超过复制上限 {_COPY_LIMIT_BYTES // (1024**3)} GiB，"
                "已停止复制以保护宿主磁盘",
            }
    return None


def independent_verify(
    payload: dict[str, Any],
    *,
    timeout_seconds: int = settings.replay_timeout_seconds,
    replay_python: str = settings.replay_python,
) -> dict[str, Any]:
    """固定原始正文验证解析行为；语法检查由前置步骤独立负责。"""
    import hashlib
    import json
    import shutil
    from pathlib import Path

    from app.infrastructure.filesystem.safe_cleanup import (
        remove_tree,
        temporary_directory,
    )
    from app.agents.review.tools.verify_issue.assessment import (
        assessment,
        changed_functions,
        parser_configuration_changes,
    )

    files = [str(item) for item in payload.get("changed_files") or []]
    workspace = Path(str(payload.get("ai_workspace_path") or ""))
    if not workspace.is_dir():
        return {
            "passed": False,
            "status": "unverified",
            "mode": "unavailable",
            "reason": "验证工作区不存在",
        }
    if not files:
        return {
            "passed": False,
            "status": "unverified",
            "mode": "unavailable",
            "reason": "没有可核对的修改文件",
        }
    # response / track_config 既可能在记录顶层，也可能嵌在 req 对象里。
    normalized_log = payload.get("normalized_log")
    if not isinstance(normalized_log, dict):
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "reason": "缺少原始问题记录，无法执行业务验证",
        }
    carrier = str(normalized_log.get("carrier") or payload.get("carrier") or "")
    req_data = normalized_log.get("req", {})
    if isinstance(req_data, str):
        try:
            req_data = json.loads(req_data)
        except json.JSONDecodeError:
            req_data = {}
    if not isinstance(req_data, dict):
        req_data = {}
    response_data = normalized_log.get("response") or req_data.get("response")
    from app.agents.review.tools.verify_issue.replay_model import extract_track_config

    track_config_data = extract_track_config(normalized_log)
    if not isinstance(response_data, str):
        response_data = (
            json.dumps(response_data, ensure_ascii=False) if response_data else ""
        )
    if not isinstance(track_config_data, str):
        track_config_data = (
            json.dumps(track_config_data, ensure_ascii=False)
            if track_config_data
            else ""
        )
    original_error = str(
        normalized_log.get("error") or normalized_log.get("raw_error") or ""
    )
    from app.agents.review.tools.verify_issue.replay_model import request_numbers
    from app.agents.review.tools.verify_issue.replay_runner import run_replay
    from app.agents.review.tools.verify_issue.semantic_compare import ReplayOutcome, compare

    if not (carrier and req_data and request_numbers(req_data)):
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "reason": "缺少运输商或有效原始请求，未执行业务验证",
        }
    if not original_error.strip():
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "reason": "缺少原始异常，无法判断基线是否复现问题",
        }
    if not response_data and not payload.get("allow_live_replay", False):
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "replay_input_mode": "missing_response",
            "replay_verdict": "missing_evidence",
            "reason": "没有录制响应，未发起不稳定的在线请求；需补充原始响应或人工确认，语法检查不能代替业务验证",
        }
    from app.agents.review.tools.verify_issue.replay_runner import resolve_interpreter

    try:
        resolve_interpreter(replay_python)
    except FileNotFoundError as error:
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "replay_input_mode": "recorded" if response_data else "live",
            "replay_verdict": "environment",
            "reason": str(error),
            "replay_exception_type": "FileNotFoundError",
        }

    # 共享树是给模型阅读的脱敏快照：_sanitize_shared_tree 只保留 .py/.json 等
    # 白名单后缀，会删除 py_rules_new.zip 这类运行资产。重放必须在未脱敏的
    # 干净 Git 业务目录上建立基线，不能复用共享树。
    baseline_root = None
    for key in ("clean_source_path", "source_project_path"):
        configured = str(payload.get(key) or "").strip()
        if not configured:
            continue
        candidate = Path(configured).resolve()
        if candidate.is_dir() and any(
            (candidate / name).is_dir() for name in ("gathers", "spiders")
        ):
            baseline_root = candidate
            break
    if baseline_root is None:
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "reason": "重放源目录不可用，无法建立独立镜像，业务修复未验证",
        }
    baseline_dir = Path(str(payload.get("baseline_dir") or ""))
    if not payload.get("baseline_dir") or not baseline_dir.is_dir():
        return {
            "passed": False,
            "status": "unverified",
            "mode": "response_replay",
            "reason": "缺少本次修复前源码快照，不允许把当前源码猜作基线",
        }
    functions = changed_functions(workspace, baseline_dir, files)
    # 重放需要 server_dev、framework 和公共库；任务工作区只有目标 carrier。
    # 两侧使用独立文件副本，避免业务运行写文件污染源仓库或另一侧重放。
    with temporary_directory(
        "ai-replay-workspace-", parent=baseline_root.parent
    ) as directory:
        replay_root = Path(directory) / "repo"
        oversized = _copy_limit_failure(baseline_root, "重放源目录")
        if oversized:
            return oversized
        shutil.copytree(
            baseline_root, replay_root, ignore=shutil.ignore_patterns(".git")
        )
        _restore_write_permissions(replay_root)
        gathers_root = replay_root / "gathers"

        # 基线重放：装入修复前的 carrier 源码快照，用未修复的代码跑同一输入。
        if not _install_baseline_carrier(
            replay_root, str(payload.get("baseline_dir") or ""), carrier
        ):
            return {
                "passed": False,
                "status": "unverified",
                "mode": "response_replay",
                "reason": "修复前源码快照不可用，无法建立基线",
            }
        fixed_root = Path(directory) / "fixed"
        # 修复后副本必须在基线重放之前复制：基线重放跑的是未修复代码，会在
        # 重放工作区里写业务输出/缓存，重放后再复制会把这些残留带进"修复后"
        # 重放，破坏两侧独立性；体积上限也会把基线产生的写入算进修复副本。
        # baseline_only 分支用不到这份副本，保持不复制。
        if not payload.get("baseline_only"):
            oversized = _copy_limit_failure(replay_root, "重放工作区快照")
            if oversized:
                return oversized
            shutil.copytree(replay_root, fixed_root)
        baseline_result = run_replay(
            carrier=carrier,
            req_json=json.dumps(req_data, ensure_ascii=False),
            response_json=response_data,
            track_config_json=track_config_data,
            gathers_root=gathers_root,
            timeout_seconds=timeout_seconds,
            python_executable=replay_python,
        )

        if payload.get("baseline_only"):
            from app.agents.review.tools.verify_issue.semantic_compare import _same_error

            observed = (
                ReplayOutcome.from_result(baseline_result.result)
                if baseline_result.success
                else ReplayOutcome.from_failure(
                    baseline_result.error, baseline_result.exception_type
                )
            )
            matched = observed.executed and _same_error(observed, original_error)
            return {
                "passed": matched,
                "status": "verified" if matched else "unverified",
                "reason": "基线已复现原始异常"
                if matched
                else "基线未复现原始异常，禁止开始修复",
                "retryable": not observed.executed,
                "observation": observed.summary(),
                "replay_output": baseline_result.output,
                "replay_error": baseline_result.error,
            }
        # 修复后重放：把 AI 工作区的 carrier 覆盖进重放工作区，跑同一输入。
        for framework in ("gathers", "spiders"):
            source_carrier = WorkspaceManager.find_carrier_dir(
                workspace, framework, carrier
            )
            replay_carrier = WorkspaceManager.find_carrier_dir(
                fixed_root, framework, carrier
            )
            if source_carrier is None:
                continue
            destination = replay_carrier or fixed_root / framework / source_carrier.name
            if destination.exists() and not remove_tree(destination):
                return {
                    "passed": False,
                    "status": "unverified",
                    "mode": "response_replay",
                    "reason": "重放工作区 carrier 清理失败",
                }
            shutil.copytree(source_carrier, destination, copy_function=shutil.copy2)
        fixed_result = run_replay(
            carrier=carrier,
            req_json=json.dumps(req_data, ensure_ascii=False),
            response_json=response_data,
            track_config_json=track_config_data,
            gathers_root=fixed_root / "gathers",
            timeout_seconds=timeout_seconds,
            python_executable=replay_python,
        )

    baseline_outcome = (
        ReplayOutcome.from_result(baseline_result.result)
        if baseline_result.success
        else ReplayOutcome.from_failure(
            baseline_result.error, baseline_result.exception_type
        )
    )
    fixed_outcome = (
        ReplayOutcome.from_result(fixed_result.result)
        if fixed_result.success
        else ReplayOutcome.from_failure(fixed_result.error, fixed_result.exception_type)
    )
    comparison = compare(
        baseline=baseline_outcome,
        fixed=fixed_outcome,
        original_error=original_error,
        live=not bool(response_data),
        expected_numbers=request_numbers(req_data),
        captured_response=response_data,
    )
    verification = assessment(
        verdict=comparison.verdict,
        passed=comparison.passed,
        functions=functions,
        original_error=original_error,
        response_sha256=hashlib.sha256(response_data.encode("utf-8")).hexdigest(),
        comparison=comparison.details,
        parser_configuration_changes=parser_configuration_changes(
            workspace, baseline_dir, files
        ),
    )
    passed = comparison.passed
    reason = comparison.reason
    # 全量原文进入脱敏后的验证产物；步骤投影只负责摘要。
    report: dict[str, Any] = {
        "passed": passed,
        "status": "verified"
        if passed
        else (
            "failed" if comparison.verified and not comparison.passed else "unverified"
        ),
        "mode": "response_replay",
        "reason": reason,
        "verification": verification,
        "replay_recipe": {
            "description": "在相同业务依赖根目录重建 input.json 并运行平台脚本；历史临时路径已清理，不能直接复制 command 重跑",
            "input": {
                "carrier": carrier,
                "req": req_data,
                "response": response_data,
                "track_config": json.loads(track_config_data or "{}"),
            },
            "entry_script": baseline_result.command[2]
            if baseline_result.command
            else "",
            "model_builder": Path(__file__)
            .parents[1].joinpath("agents/review/tools", "verify_issue/replay_model.py")
            .read_text(encoding="utf-8"),
            "runtime_adapter": Path(__file__)
            .parents[1].joinpath("agents/review/tools", "verify_issue/replay_runtime.py")
            .read_text(encoding="utf-8"),
            "business_observer": Path(__file__)
            .parents[1].joinpath("agents/review/tools", "verify_issue/business_observation.py")
            .read_text(encoding="utf-8"),
        },
        "replay_verdict": comparison.verdict,
        "replay_input_mode": "live" if not response_data else "recorded",
        "replay_comparison": comparison.details,
        "replay_baseline_output": baseline_result.output,
        "replay_output": fixed_result.output,
        "replay_full_output": True,
        "replay_baseline_stdout": getattr(baseline_result, "stdout", ""),
        "replay_baseline_stderr": getattr(baseline_result, "stderr", ""),
        "replay_baseline_error": baseline_result.error,
        "replay_baseline_exception_type": baseline_result.exception_type,
        "replay_baseline_traceback": baseline_result.traceback,
        "replay_baseline_returncode": getattr(baseline_result, "returncode", None),
        "replay_stdout": getattr(fixed_result, "stdout", ""),
        "replay_stderr": getattr(fixed_result, "stderr", ""),
        "replay_baseline_command": getattr(baseline_result, "command", []),
        "replay_command": getattr(fixed_result, "command", []),
        "replay_returncode": getattr(fixed_result, "returncode", None),
        "replay_error": fixed_result.error,
        "replay_exception_type": fixed_result.exception_type,
        "replay_traceback": fixed_result.traceback,
    }
    return report


def _restore_write_permissions(root: Path) -> None:
    """递归恢复重放工作区的写权限（文件 0o644 / 目录 0o755）。

    ``source_project_path`` 指向的共享干净树整棵被设为只读（防止任务通过硬链接
    改写共享内容），``shutil.copytree`` 会把每个文件的原有只读位原样复制下来，
    只读属性随树"传播"。重放工作区是本次执行的独立副本，业务进程要在其中写
    缓存、日志和临时文件，必须在复制后立即恢复当前用户的写位。
    """
    import os
    import stat

    for current, _directories, filenames in os.walk(root):
        directory = Path(current)
        try:
            mode = stat.S_IMODE(directory.stat().st_mode)
            os.chmod(directory, mode | stat.S_IWRITE | stat.S_IXUSR)
        except OSError:
            pass
        for name in filenames:
            path = directory / name
            if path.is_symlink():
                continue
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
                os.chmod(path, mode | stat.S_IWRITE)
            except OSError:
                pass


def _install_baseline_carrier(
    replay_root: Path, baseline_dir: str, carrier: str
) -> bool:
    """把修复前的 carrier 源码基线装进重放工作区。

    基线快照必须来自本次修改前的实际源码；没有快照就返回 False，
    不把当前源仓库的状态猜作修复前代码。
    """
    import shutil

    from app.infrastructure.filesystem.safe_cleanup import remove_tree

    if not baseline_dir.strip():
        return False
    root = Path(baseline_dir).resolve()
    if not root.is_dir():
        return False
    installed = False
    for framework in ("gathers", "spiders"):
        source = WorkspaceManager.find_carrier_dir(root, framework, carrier)
        if source is None:
            continue
        target = WorkspaceManager.find_carrier_dir(replay_root, framework, carrier)
        if target is None:
            target = replay_root / framework / source.name
        if (
            source == target.resolve()
            or source.is_relative_to(target.resolve())
            or target.resolve().is_relative_to(source)
        ):
            return False
        if target.exists() and not remove_tree(target):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, copy_function=shutil.copy2)
        installed = True
    return installed
