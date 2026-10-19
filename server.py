"""服务入口。

    python server.py
"""

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from yq_common.fastapi_utils.fastapi_utils import FastAPIUtils

from workbench.api import SERVICE_VERSION, router

app = FastAPIUtils.setup_fastapi_server("py.ai.bug.fixer")
app.title = "AI Repair Workbench API"
app.version = SERVICE_VERSION
app.description = "Run/task/step/artifact observability and human approval API."
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(app=app, host="0.0.0.0", port=8000, access_log=True)