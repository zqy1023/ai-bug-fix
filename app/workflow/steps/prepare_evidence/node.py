"""05 确定性预分析，不调用模型。"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceManager
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
        raise StepFailed("异常内容没有可用于源码检索的有效 token")
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
        category=_category(data.problem),
        extracted=_extract(data.problem),
        legacy_only=legacy_only,
        legacy_reason=_legacy_reason(safe_carrier, [carrier_dir]) if legacy_only else "",
    )


