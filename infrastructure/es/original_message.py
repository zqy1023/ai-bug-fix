"""按精确请求身份关联原始异常；不拼装、不裁剪或改写日志正文。"""
from __future__ import annotations

import json
import re
from typing import Any

_TRUNCATED = re.compile(r"…?\(已截断，原长 \d+ 字符\)")
_FLOW = re.compile(r"^\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+)\s+\[([^\]]+)\]")


def _numbers(problem: dict[str, Any]) -> set[str]:
    request = problem.get("req")
    try:
        request = json.loads(request) if isinstance(request, str) else request
    except ValueError:
        request = {}
    if isinstance(request, dict):
        requests = request.get("requests")
        requests = requests if isinstance(requests, list) else []
        values = [request.get("number"), *(item.get("number") for item in requests if isinstance(item, dict))]
        numbers = {str(value) for value in values if value is not None and str(value)}
        if numbers:
            return numbers
    return set(re.findall(r'\bnumber\s*:\s*([A-Za-z0-9_-]+)', str(problem.get("message") or ""), re.IGNORECASE))


def _same_flow(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a = _FLOW.match(str(left.get("message") or ""))
    b = _FLOW.match(str(right.get("message") or ""))
    return bool(a and b and a.groups() == b.groups()
                and left.get("carrier") == right.get("carrier")
                and left.get("service_name") == right.get("service_name")
                and _numbers(left) & _numbers(right))


def original_message(problem: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """从无损归档恢复日志，优先展示同一请求的完整 model 解析异常。"""
    sources = [dict(record.get("problem") or record) for record in records if isinstance(record, dict)]
    direct = str(problem.get("message") or "")
    anchor = dict(problem)
    truncated = bool(_TRUNCATED.search(direct))
    if truncated or not direct:
        prefix = _TRUNCATED.split(direct)[0].rstrip("…")
        matches = [item for item in sources if all(item.get(key) == problem.get(key) for key in ("carrier", "biztype", "service_name"))
                   and (str(item.get("message") or "").startswith(prefix) if prefix else item.get("error") == problem.get("error"))
                   and (not problem.get("req") or item.get("req") == problem.get("req"))]
        if len(matches) == 1:
            anchor = matches[0]
    candidates = [item for item in sources if _same_flow(anchor, item)]
    models = {str(item["message"]): item for item in candidates
              if item.get("message_format") == "model" and item.get("message")}
    chosen = next(iter(models.values())) if len(models) == 1 else anchor
    message = str(chosen.get("message") or "")
    related = list(dict.fromkeys(str(item.get("message") or "") for item in [anchor, *candidates]
                                if item.get("message") and item.get("message") != message))
    return {"message": message, "trigger_message": str(anchor.get("message") or ""),
            "related_messages": related, "truncated": bool(_TRUNCATED.search(message)),
            "from_archive": direct != message,
            "source_biztype": str(chosen.get("biztype") or ""),
            "message_format": str(chosen.get("message_format") or "")}
