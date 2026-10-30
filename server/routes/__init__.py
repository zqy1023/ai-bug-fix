"""业务接口路由聚合中心。"""
from __future__ import annotations

from fastapi import APIRouter

from .common import SERVICE_NAME, SERVICE_VERSION
from .runs import router as runs_router
from .tasks import router as tasks_router
from .artifacts import router as artifacts_router

router = APIRouter()
router.include_router(runs_router)
router.include_router(tasks_router)
router.include_router(artifacts_router)

__all__ = [
    "router",
    "runs_router",
    "tasks_router",
    "artifacts_router",
    "SERVICE_NAME",
    "SERVICE_VERSION",
]
