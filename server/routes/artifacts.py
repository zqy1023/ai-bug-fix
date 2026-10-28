"""产物下载与内容展示路由。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse

from server.routes.common import *

router = APIRouter()

@router.get("/api/v1/runs/{run_id}/artifacts/download")
def download_artifact(run_id: str, path: str = Query(..., min_length=1, max_length=500),
                      task_id: str = "", _: None = Depends(_authorize)) -> Response:
    """下载完整归档产物；与预览使用同一权限和目录边界，无预览大小限制。"""
    store = _store()
    run_id = _safe_id(run_id)
    if task_id:
        task_id = _safe_id(task_id)
    base = (store.task_dir(run_id, task_id) if task_id else store.run_dir(run_id)).resolve()
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="非法产物路径")
    if path == "metrics/agent.jsonl":
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                          for row in store.read_metrics(run_id, task_id))
        return Response(content, media_type="application/x-ndjson", headers={
            "Content-Disposition": 'attachment; filename="agent.jsonl"',
        })
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@router.get("/api/v1/runs/{run_id}/artifacts")
def v1_list_artifacts(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    detail = _v1_detail(run_id)
    return {"items": detail["artifacts"], "total": len(detail["artifacts"])}


@router.get("/api/v1/runs/{run_id}/artifacts/content")
def v1_artifact_content(
    run_id: str, path: str = Query(..., min_length=1, max_length=500), task_id: str = "",
    _: None = Depends(_authorize),
) -> JSONResponse:
    return _artifact_content(run_id, path, task_id)

