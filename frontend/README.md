# AI 异常日志自动修复工作台（前端）

本项目采用 React、TypeScript、Vite、Ant Design、TanStack Query、React Flow 和 ECharts。前端不直接访问工作目录、steps.json、Git 仓库或 LangGraph checkpoint；所有读写必须经过受控后端 API。

## 当前页面

- 运行总览：活跃执行、待审批、成功/失败统计、7 天趋势、最近执行。
- 执行记录：按状态和执行 ID 查询、分页、创建执行、进入执行详情。
- 运行控制台：展示真实运行统计、近 7 天趋势、最近执行；显示源仓库未配置等启动前置条件。
- 执行详情：点击工作流节点打开检查器，查看服务端持久化的输入、输出、耗时、尝试次数与错误；产物可通过受控 API 按需预览。
- 执行控制：支持刷新、服务端允许时恢复执行、提交停止请求；停止操作明确展示为请求，不冒充即时中断。
- 人工审批与人工介入队列分离：有明确方案的审批可以批准/拒绝；MANUAL 任务进入人工介入队列，不伪装成待审批方案。
- 模型调用审计按根因定位、代码修复、变更审查 Agent 筛选；按任务过滤公开事件时间线、工具调用、调用耗时、Token 与最终输出。隐藏推理链不被持久化。运行详情通过 SSE 快照更新，并保留轮询兜底。
- 导航栏显示实时待审批数量与后端健康状态；后端启用 Bearer Token 时可在界面输入令牌，令牌只保存在当前页面内存，不写入构建产物或浏览器持久化存储。

## 本地开发

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

默认 API 代理指向 http://127.0.0.1:8000。生产构建使用 npm run typecheck 和 npm run build。发布前必须在项目环境实际运行这两项命令并执行端到端验证；本次远程提交环境无法执行 npm 构建。

## 当前 API 契约

所有端点以 /api/v1 为前缀，并要求后端执行身份认证、授权、输入校验、审计和幂等控制。

| 方法 | 路径 | 责任 |
|---|---|---|
| GET | /dashboard/summary | 返回概览统计与趋势 |
| GET | /runs?page=&page_size=&status=&keyword=&from=&to= | 分页查询执行 |
| POST | /runs | 创建执行，返回 run_id；必须有请求幂等键 |
| GET | /runs/{run_id} | 返回执行元数据、步骤、任务、产物引用、恢复能力 |
| POST | /runs/{run_id}/resume | 仅恢复可恢复且有权限的执行 |
| POST | /runs/{run_id}/cancel | 提交停止请求，不得谎报已立即终止 |
| GET | /approvals?status=pending | 获取当前用户有权处理的审批 |
| POST | /approvals/{approval_id}/decision | 提交 approve/reject 与理由；后端校验审批状态与授权范围 |

响应错误建议统一为 { "code": "...", "message": "...", "request_id": "..." }。分页响应为 { "items": [], "total": 0, "page": 1, "page_size": 20 }。

## 与现有工作流的集成约束

1. 当前 Python 入口是 CLI/LangGraph，不等于 Web API；此目录先建立独立前端和类型化 API 客户端，后端路由位于 `workbench/api.py`；模型调用审计从 Provider 层写入运行目录，SSE 以运行记录快照推送。真实部署仍须执行集成与端到端测试。
2. 节点图以服务端返回的步骤记录为状态来源；图上的连线是流程导航示意，不替代后端实际分支和审计数据。
3. waiting、failed、业务拒绝、人工转交、取消请求必须区分，不能全部映射成失败。
4. 前端隐藏按钮不是权限控制。后端必须对每个读写端点授权，审批和交付必须服务端二次校验。
5. 产物只传引用和受控下载地址；不得把密钥、原始凭据或任意本地路径暴露给浏览器。
6. 恢复、停止、审批与启动等副作用操作不做客户端自动重试；后端通过幂等键、状态机和审计记录防止重复执行。

## 后续生产化必做

- FastAPI 控制面：执行提交队列、状态查询、审批恢复、取消信号、产物授权读取。
- 认证/权限：接入公司 SSO/OIDC；按角色区分查看、发起、审批、交付权限；审计操作者和理由。
- 实时事件：优先 SSE 推送节点状态、心跳、重连游标；断线后以服务端快照恢复。
- API 与 E2E 测试：覆盖重复提交、刷新/断线、并发审批、过期审批、无权限、执行中取消、checkpoint 恢复和交付失败。
- 部署：Nginx/Ingress 同源代理、CSP、安全响应头、访问日志、健康检查和版本标识。
## Docker 生产构建

项目根目录提供后端 `Dockerfile`、`docker-compose.yml`，前端使用 `frontend/Dockerfile` 与 `frontend/nginx.conf`。复制并填写根目录 `.env.example`，特别设置 `WORKBENCH_API_TOKEN`、`AI_REPAIR_SOURCE_REPO_HOST_PATH` 和 `AI_REPAIR_RUNTIME_HOST_PATH`，再执行：

```bash
docker compose config
docker compose up -d --build
docker compose logs -f api frontend
```

构建机必须能够访问 requirements 中的内部 `yq_common` 包仓库。远程访问应在外部 HTTPS 反向代理后进行，不要直接向公网暴露 API 端口。

## 浅色企业级工作台升级

本次升级分支采用白色导航栏、浅灰工作区、蓝色主操作和轻量边界的浅色视觉规范，不使用深色侧栏。视觉变更不改变现有 API 的真实状态来源。

企业级工作台与知识沉淀的目标、数据治理要求、安全边界和验收清单见 [企业级工作台升级实施规范](../docs/enterprise-workbench-upgrade.md)。当前分支已实现知识案例的 SQLite 持久化、候选登记/发布门禁/停用、从任务记录沉淀候选案例，以及在 `prepare_evidence` 阶段向根因定位 Agent 注入已发布案例。知识召回目前是轻量确定性排序；自动沉淀、知识收益统计和完整生产验收仍需继续实现。远程提交不能替代本地 `npm run typecheck`、`npm run build` 和端到端测试。
