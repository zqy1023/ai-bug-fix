# AI Repair Workbench

唯一正式前端位于 `frontend/`，后端使用 FastAPI，执行引擎复用仓库现有 LangGraph 主图、步骤插件、RecordStore 与 checkpoint。已退役的 `workbench/ui/` 不再作为启动或构建入口。

## 本地启动

终端一（仓库根目录）：

```powershell
python -m pip install -r requirements.txt
uvicorn workbench.api:app --host 127.0.0.1 --port 8000
```

终端二：

```powershell
cd frontend
npm install
npm run dev
```

访问 Vite 输出的地址，默认 `http://127.0.0.1:5173`。API 文档：`http://127.0.0.1:8000/api/docs`。

生产构建：

```powershell
cd frontend
npm run typecheck
npm run build
```

## 工作台能力

- 运行创建、列表、详情、配置与任务进度。
- 节点时间线及输入、输出、错误和产物引用。
- Provider 请求级模型调用审计：模型、Prompt 哈希、耗时、Token 用量（供应商未返回时为空）、工具调用统计与错误类型。
- 运行状态快照 SSE 和控制操作审计查询。
- 审批队列与 LangGraph checkpoint 恢复。
- 产物读取限制在当前运行/任务目录，限制最大读取大小。

模型调用审计文件写入 `runtime/runs/<run_id>/model_calls.jsonl`。历史运行不会补造缺失的模型调用记录。Token 等用量字段以 Provider 实际返回为准。

## 运行与安全边界

- 默认仅监听回环地址；远程部署必须设置 `WORKBENCH_API_TOKEN` 并使用 HTTPS 反向代理。
- 运行记录目录应位于持久化磁盘；同时备份 `workflow-checkpoints.sqlite3`、`workbench-control.sqlite3` 与运行产物。
- 工作台不自动合并代码；代码交付仍遵守工作流门禁。
- 当前 SSE 发送的是基于持久化运行记录的状态快照，不等于逐 token 模型流。取消接口目前表达停止请求，当前节点可能完成后才响应。
- 本仓库尚未提供完整的企业 SSO/OIDC 与 RBAC；远程部署前必须补齐统一身份认证、角色授权、反向代理安全策略和端到端验收。

## Docker 部署

生产容器入口为根目录 `docker-compose.yml`。详细步骤见 `docs/WORKBENCH_OPERATIONS.md`。前端审计可按 Agent 和当前选中的任务过滤，公开事件包含模型输出、工具调用、工具结果和耗时；不会展示隐藏推理链。MANUAL 任务作为终态记录展示，不进入交付审批队列。
