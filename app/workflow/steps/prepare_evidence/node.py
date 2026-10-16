"""05 确定性预分析，不调用模型。"""

from __future__ import annotations

import ast
import re
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.infrastructure.config import load_config
from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.prepare_evidence.models import EvidenceInput, EvidenceOutput
from app.domain.policies import is_registered_service
from app.agents.locate.config import LocateConfig
from config.settings import settings

_STOPWORDS = {
    "none", "true", "false", "self", "http", "https", "www", "com", "null",
    "list", "dict", "str", "int", "type", "this", "that", "with", "from",
    "import", "class", "return", "line", "file", "path", "code", "text",
    "info", "warn", "logs", "log", "message", "result", "value", "object",
}
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _tokens(text: str) -> set[str]:
    """把文本切成可检索的小写 token（拆 camelCase 与下划线）。"""
    spaced = _CAMEL.sub(" ", text)
    out: set[str] = set()
    for part in _WORD_SPLIT.split(spaced):
        token = part.lower()
        if len(token) >= 4 and token not in _STOPWORDS:
            out.add(token)
    return out


def _query_tokens(problem: dict[str, Any]) -> set[str]:
    """从异常输入里提取检索词；error 与 carrier 信号最强。"""
    chunks: list[str] = []
    for key, value in problem.items():
        if isinstance(value, str) and value:
            chunks.append(value[:settings.evidence_query_value_chars])
    return _tokens(" ".join(chunks))


def _rank_candidates(
    root: Path,
    files: list[str],
    problem: dict[str, Any],
    skipped_large_files: list[str],
) -> list[str]:
    """按"异常文本 × 文件名/内容"的相关度排序候选文件。

    纯字母序取前 ``max_candidates`` 个只是碰运气：真正相关的主脚本常常排在字母序
    后面。这里用稀有度加权的 token 命中打分——越少见的 token 命中权重越高，文件名
    命中额外加权，让模型第一轮就落在最相关的文件上。
    """
    query = _query_tokens(problem)
    if not query:
        # 即使异常文本几乎没有可检索词，也要为只读分析 Agent 提供真实源码；
        # 同时保留单文件大小门禁，避免异常大文件进入模型上下文。
        available: list[str] = []
        for relative in files:
            try:
                size = (root / relative).stat().st_size
            except OSError as error:
                raise StepFailed(f"读取候选源码元数据失败: {relative}: {error}") from error
            if size > settings.evidence_max_read_bytes:
                skipped_large_files.append(relative)
                continue
            available.append(relative)
        return sorted(
            available,
            key=lambda path: (0 if Path(path).name in {"main.py", "__init__.py"} else 1, path),
        )
    scored: list[tuple[str, float]] = []
    df: dict[str, int] = {}
    hits_per_file: dict[str, set[str]] = {}
    stem_hits: dict[str, set[str]] = {}
    for relative in files:
        target = root / relative
        try:
            if target.stat().st_size > settings.evidence_max_read_bytes:
                content = ""
                skipped_large_files.append(relative)
            else:
                content = target.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError as error:
            raise StepFailed(f"读取证据文件失败: {relative}: {error}") from error
        stem = Path(relative).stem.lower()
        content_hits = {token for token in query if token in content}
        stem_hit_set = {token for token in query if token in stem}
        hits = content_hits | stem_hit_set
        hits_per_file[relative] = hits
        stem_hits[relative] = stem_hit_set
        for token in hits:
            df[token] = df.get(token, 0) + 1
    for relative, hits in hits_per_file.items():
        rarity = sum(1.0 / df[token] for token in hits)
        score = rarity + 2.0 * len(stem_hits[relative])
        scored.append((relative, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [relative for relative, _ in scored]


_CATEGORIES = (
    ("网络/上游响应", ("timeout", "502", "503", "cloudflare", "request", "proxy")),
    ("HTML/XML/JSON 解析", ("json", "xml", "html", "parse", "decode", "keyerror")),
    ("时间和地址格式", ("time", "timezone", "address", "location", "date")),
    ("注册/路由", ("register", "plugin", "route", "carrier", "gather")),
    ("类型和模型契约", ("type", "model", "schema", "validation", "attribute")),
)


def _category(problem: dict[str, Any]) -> str:
    text = " ".join(str(value).lower() for value in problem.values())
    for title, keywords in _CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return title
    return "代码异常"


def _extract(problem: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("carrier", "gather_channel", "number", "param", "service_name"):
        value = problem.get(key)
        if isinstance(value, (str, int, float)) and str(value):
            result[key] = str(value)[:settings.evidence_extracted_value_chars]
    return result


def _references_legacy(new_root: Path, legacy_root: Path) -> bool:
    """检测新 carrier 源码是否显式引用旧 spiders 实现。"""
    if not new_root.is_dir() or not legacy_root.is_dir():
        return False
    markers = ("from spiders", "import spiders", "spiders.", "spiders/")
    for source in sorted(new_root.rglob("*.py")):
        if not source.is_file() or source.is_symlink():
            continue
        try:
            content = source.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError as error:
            raise StepFailed(f"读取旧框架引用证据失败: {source}: {error}") from error
        if any(marker in content for marker in markers):
            return True
    return False


def _legacy_reason(carrier: str, chain: list[str]) -> str:
    """旧框架转人工的原因文案，报告里直接可读。"""
    missing_entry = f"缺少新框架入口 gathers/{carrier}/main.py"
    if chain:
        where = "、".join(chain[:3])
        return (
            f"carrier {carrier} {missing_entry}，采集链路会执行旧框架 spiders/ 代码"
            f"（转发点：{where}），改 gathers/ 无法生效，转人工处理"
        )
    return (
        f"目标 carrier 只有旧框架 spiders/{carrier} 实现，{missing_entry}，转人工处理"
    )



def _published_knowledge(problem: dict[str, Any], carrier: str, biztype: str, category: str) -> list[dict[str, str]]:
    """读取已发布案例并做轻量确定性排序；知识检索失败不阻断主修复流程。"""
    try:
        runtime_dir = load_config().runtime_dir
        database = runtime_dir / "workbench-control.sqlite3"
        if not database.is_file():
            return []
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """SELECT case_id,title,fingerprint,carrier,biztype,root_cause,evidence,change_summary,
                          source_run_id,updated_at
                   FROM repair_knowledge_cases WHERE status='published'
                   ORDER BY updated_at DESC LIMIT 500"""
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error, AttributeError, ValueError):
        return []

    query = _query_tokens(problem)
    error_text = str(problem.get("error") or "")
    fingerprint = sha256(error_text.encode("utf-8")).hexdigest() if error_text else ""
    ranked: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        item = dict(row)
        score = 0
        if fingerprint and item.get("fingerprint") == fingerprint:
            score += 30
        if item.get("carrier") and item["carrier"] == carrier:
            score += 12
        if item.get("biztype") and item["biztype"] == biztype:
            score += 10
        searchable = " ".join(str(item.get(key) or "") for key in
                              ("title", "fingerprint", "root_cause", "evidence", "change_summary", "biztype"))
        score += min(len(query & _tokens(searchable)), 8)
        if category and category.lower() in searchable.lower():
            score += 2
        if score <= 0:
            continue
        ranked.append((score, {
            "case_id": str(item["case_id"]),
            "title": str(item["title"])[:200],
            "carrier": str(item["carrier"] or ""),
            "biztype": str(item["biztype"] or ""),
            "fingerprint": str(item["fingerprint"] or "")[:500],
            "root_cause": str(item["root_cause"] or "")[:1500],
            "evidence": str(item["evidence"] or "")[:1200],
            "change_summary": str(item["change_summary"] or "")[:1500],
            "source_run_id": str(item["source_run_id"] or ""),
            "updated_at": str(item["updated_at"] or ""),
            "match_score": str(score),
        }))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["case_id"]))
    # 知识检索保持只读；工作流不再自动记录使用轨迹或沉淀新案例。
    selected = ranked[:3]
    return [item for _, item in selected]

def run(ctx: StepContext, data: EvidenceInput) -> EvidenceOutput:
    """定位唯一 carrier 目录并返回有限候选文件。旧框架任务不进入修复。"""
    if not is_registered_service(data.service_name):
        raise StepFailed(f"service_name 未注册，禁止准备修复证据: {data.service_name}")
    if not data.source_project_path:
        raise StepFailed("缺少 prepare_source_snapshot 提供的源项目路径")
    root = Path(data.source_project_path).resolve()
    if not root.is_dir():
        raise StepFailed(f"源项目路径不存在: {root}")
    safe_carrier = WorkspaceManager.safe_carrier_name(data.carrier)
    new_entry = root / "gathers" / safe_carrier / "main.py"
    legacy_dir = root / "spiders" / safe_carrier
    # 唯一框架门禁：新框架 main.py 存在即进入新框架；否则只在旧目录存在时转人工。
    if data.framework == "gathers":
        if not new_entry.is_file():
            raise StepFailed(f"快照框架为 gathers，但入口不存在: gathers/{safe_carrier}/main.py")
        carrier_root = new_entry.parent
        carrier_dir = carrier_root.relative_to(root).as_posix()
    else:
        if new_entry.is_file() or not legacy_dir.is_dir():
            raise StepFailed("快照框架与源项目当前 carrier 入口不一致")
        carrier_root = legacy_dir
        carrier_dir = legacy_dir.relative_to(root).as_posix()
    # 旧框架也必须生成真实候选源码证据供只读定位 Agent 分析。
    # 是否允许修复由后续路由决定；这里不提前终止分析。
    legacy_dependency = (
        data.framework == "gathers"
        and _references_legacy(root / "gathers" / safe_carrier, legacy_dir)
    )
    all_files = [
        path.relative_to(root).as_posix()
        for path in sorted(carrier_root.rglob("*.py"))
        if path.is_file() and not path.is_symlink()
    ]
    if not all_files:
        raise StepFailed(f"carrier 目录没有 Python 实现: {data.carrier}")
    if len(all_files) > settings.evidence_max_scan_files:
        raise StepFailed(
            f"carrier Python 文件数 {len(all_files)} 超过证据扫描上限 "
            f"{settings.evidence_max_scan_files}"
        )

    skipped_large_files: list[str] = []
    max_candidates = LocateConfig.load().max_candidate_files
    candidates = _rank_candidates(root, all_files, data.problem, skipped_large_files)[
        :max_candidates
    ]
    category = _category(data.problem)
    knowledge_cases = _published_knowledge(data.problem, data.carrier, data.biztype, category)
    symbols: dict[str, list[str]] = {}
    syntax_errors: dict[str, str] = {}
    for relative in candidates:
        try:
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            symbols[relative] = [
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ][: settings.evidence_max_symbols]
        except SyntaxError as error:
            symbols[relative] = []
            syntax_errors[relative] = f"line {error.lineno}: {error.msg}"
        except (OSError, UnicodeDecodeError) as error:
            raise StepFailed(f"解析证据文件失败: {relative}: {error}") from error
    legacy_only = data.framework == "spiders"
    return EvidenceOutput(
        carrier_dir=carrier_dir,
        framework=data.framework,
        candidates=candidates,
        symbols=symbols,
        skipped_large_files=skipped_large_files,
        syntax_errors=syntax_errors,
        category=category,
        knowledge_cases=knowledge_cases,
        extracted=_extract(data.problem),
        legacy_only=legacy_only,
        legacy_dependency=legacy_dependency,
        legacy_reason=(
            _legacy_reason(safe_carrier, [carrier_dir])
            if legacy_only
            else f"新框架 carrier 显式引用旧框架 spiders/{safe_carrier}；本任务仅分析、不修改。"
            if legacy_dependency
            else ""
        ),
    )


