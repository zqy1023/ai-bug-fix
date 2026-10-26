"""04 按 Git 修复分支与问题指纹判断是否可以进入本轮。"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from git import Repo
from git.exc import GitCommandError

from app.domain.errors import StepFailed
from app.domain.keys import repair_branch
from app.application.context import StepContext
from app.workflow.steps.claim_repair_slot.models import ClaimInput, ClaimOutput
from config.settings import settings


logger = logging.getLogger(__name__)

_CLAIM_TTL = timedelta(hours=settings.repair_claim_ttl_hours)
_RELEASE_ATTEMPTS = 3
_RELEASE_BACKOFF_SECONDS = 0.1


def _claim_key(source: str, branch: str, problem_key: str) -> str:
    value = "\x00".join((str(Path(source).resolve()), branch, problem_key))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _claim_connection(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(root / settings.repair_claims_db_name),
        timeout=settings.repair_claims_timeout_seconds,
    )
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={settings.repair_claims_busy_timeout_ms}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS repair_claims ("
        "claim_key TEXT PRIMARY KEY, source_repo TEXT NOT NULL, branch TEXT NOT NULL, "
        "problem_key TEXT NOT NULL, run_id TEXT NOT NULL, task_id TEXT NOT NULL, "
        "claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
    )
    return connection


def _claim_slot(ctx: StepContext, source: str, branch: str, problem_key: str, task_id: str) -> bool:
    key = _claim_key(source, branch, problem_key)
    now = datetime.now(timezone.utc)
    expires = now + _CLAIM_TTL
    connection = _claim_connection(ctx.store.root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT run_id,task_id,expires_at FROM repair_claims WHERE claim_key=?",
            (key,),
        ).fetchone()
        if row:
            try:
                expired = datetime.fromisoformat(str(row[2])) <= now
            except ValueError:
                expired = True
            if not expired and (str(row[0]) != ctx.run.run_id or str(row[1]) != task_id):
                connection.rollback()
                return False
            connection.execute("DELETE FROM repair_claims WHERE claim_key=?", (key,))
        connection.execute(
            "INSERT INTO repair_claims(claim_key,source_repo,branch,problem_key,run_id,task_id,claimed_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (key, str(Path(source).resolve()), branch, problem_key, ctx.run.run_id, task_id,
             now.isoformat(), expires.isoformat()),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def release_claim(root: str | Path, *, run_id: str, task_id: str) -> None:
    """任务进入终态后释放修复资格；其他运行的 claim 不受影响。

    释放是终态收尾的一部分，单次 DELETE 遇到 SQLite 忙/磁盘瞬时故障就让
    资格永远留在库里，后续同问题任务会被误判为"已被领取"。这里做短退避
    重试，最终仍失败则记录 error 日志（带 claim_key）并把异常交给调用方，
    由调用方决定是否中断。
    """
    root_path = Path(root)
    claim_keys: list[str] = []
    last_error: Exception | None = None
    for attempt in range(1, _RELEASE_ATTEMPTS + 1):
        connection: sqlite3.Connection | None = None
        try:
            connection = _claim_connection(root_path)
            rows = connection.execute(
                "SELECT claim_key FROM repair_claims WHERE run_id=? AND task_id=?",
                (run_id, task_id),
            ).fetchall()
            claim_keys = [str(row[0]) for row in rows] or claim_keys
            connection.execute(
                "DELETE FROM repair_claims WHERE run_id=? AND task_id=?",
                (run_id, task_id),
            )
            connection.commit()
            return
        except Exception as error:
            last_error = error
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            if attempt < _RELEASE_ATTEMPTS:
                time.sleep(_RELEASE_BACKOFF_SECONDS * attempt)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
    logger.error(
        "释放修复资格失败 run_id=%s task_id=%s claim_key=%s attempts=%s",
        run_id, task_id, ",".join(claim_keys) or "<unknown>", _RELEASE_ATTEMPTS,
        exc_info=last_error,
    )
    if last_error is not None:
        raise last_error


def renew_claim(root: str | Path, *, run_id: str, task_id: str) -> bool:
    """恢复尚未终态的任务时续租本运行已领取的资格。

    只延长本 ``run_id`` + ``task_id`` 的记录。记录已按 claim_key 为主键，
    存活即说明没有别的运行抢走同一问题（被抢走时记录已被删除或改写为对方），
    因此运行自己的记录即使刚过 TTL 也可以原样续租，否则暂停超过租期的
    运行将永远无法恢复。记录不存在时返回 ``False``，不做补领。
    """
    now = datetime.now(timezone.utc)
    expires = now + _CLAIM_TTL
    connection = _claim_connection(Path(root))
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT expires_at FROM repair_claims WHERE run_id=? AND task_id=?",
            (run_id, task_id),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        connection.execute(
            "UPDATE repair_claims SET expires_at=? WHERE run_id=? AND task_id=?",
            (expires.isoformat(), run_id, task_id),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def pending_repair_branch(
    source: str,
    *,
    biztype: str,
    carrier: str,
    problem_key: str,
    remote_name: str,
) -> str:
    """只读检查同一问题是否已有未合并修复分支。没有则返回空字符串。"""
    branch = repair_branch(
        biztype,
        carrier,
        settings.repair_branch_prefix,
        problem_key,
    )
    try:
        repo = Repo(source, search_parent_directories=True)
    except Exception as error:
        raise StepFailed(f"不是有效的本地 Git 仓库: {source}") from error
    return _pending_branch(repo, branch, remote_name)


def _effective_problem_key(data: ClaimInput) -> str:
    """problem_key 缺失时的统一回退口径：task_id 的最后一段（问题指纹短哈希）。

    分支名（``repair_branch``）与 claim 记录键必须用同一表达式，否则同一任务
    在"分支预检"和"资格占用"两边会算出不同的键。task_id 由
    ``task_id(biztype, carrier, fingerprint)`` 生成，末段就是指纹短哈希，
    回退到它与分支名的指纹段语义一致。在途历史数据不受影响：claim 记录的
    释放/续租都按 run_id + task_id 定位，不依赖 problem_key 口径。
    """
    return data.problem_key or data.task_id.rsplit("__", 1)[-1]


def run(ctx: StepContext, data: ClaimInput) -> ClaimOutput:
    """已有未合并修复分支时跳过，保证本轮处理的是干净状态。"""
    if ctx.services.source_repo is None and not data.source_repo_path:
        raise StepFailed("源仓库未配置，无法判断修复分支")
    source = data.source_repo_path or str(ctx.services.source_repo)
    # 分支名与 claim 键共用同一 problem_key 回退口径，见 _effective_problem_key。
    problem_key = _effective_problem_key(data)
    branch = repair_branch(
        data.biztype,
        data.carrier,
        settings.repair_branch_prefix,
        problem_key,
    )
    pending = pending_repair_branch(
        source,
        biztype=data.biztype,
        carrier=data.carrier,
        problem_key=problem_key,
        remote_name=ctx.services.git_remote or settings.default_git_remote,
    )
    if pending:
        raise StepFailed(
            f"存在未合并的修复分支，跳过本轮任务: {pending}",
            {"claimed": False, "result": "skipped", "repair_branch": pending},
            result="skipped",
        )

    if not _claim_slot(ctx, source, branch, problem_key, data.task_id):
        raise StepFailed(
            "同一问题已被其他运行领取，跳过本轮任务",
            {"claimed": False, "result": "skipped", "repair_branch": branch},
            result="skipped",
        )

    return ClaimOutput(claimed=True, result="claimed")


def _pending_branch(repo: Repo, branch: str, remote_name: str) -> str:
    """返回尚未合入基准分支的本地或远程修复分支名。"""
    base = _base_commit(repo, remote_name)
    names = {head.name for head in repo.heads}
    remote = f"{remote_name}/{branch}"
    if remote in {ref.name for ref in repo.refs}:
        names.add(remote)
    for name in (branch, remote):
        if name not in names:
            continue
        try:
            repo.git.merge_base("--is-ancestor", name, base)
        except GitCommandError as error:
            # merge-base --is-ancestor: 0=已合入，1=未合入。只有 1 才代表
            # "存在未合并修复分支"；其它状态是 Git 本身故障，不能静默当成
            # 已合并而跳过清理，也不能把它误判成业务跳过。
            if getattr(error, "status", None) == 1:
                return name
            raise StepFailed(
                "git 探测失败（可重试）",
                {
                    "branch": name,
                    "base": base,
                    "git_status": getattr(error, "status", None),
                },
            ) from error
    return ""


def _base_commit(repo: Repo, remote_name: str) -> str:
    """修复分支应合入的基准。优先远程默认分支，没有 remote 时用本地 HEAD。"""
    remote_refs = {ref.name for ref in repo.refs if ref.name.startswith(f"{remote_name}/")}
    for name in (f"{remote_name}/main", f"{remote_name}/master"):
        if name in remote_refs:
            return name
    return "HEAD"


__all__ = ["pending_repair_branch", "release_claim", "renew_claim", "run"]
