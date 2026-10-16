# AI 异常日志自动修复工作台（前端）

本项目采用 React、TypeScript、Vite、Ant Design、TanStack Query、React Flow 和 ECharts。前端不直接访问工作目录、steps.json、Git 仓库或 LangGraph checkpoint；所有读写必须经过受控后端 API。

## 当前页面

- 运行总览：活跃执行、待审批、成功/失败统计、7 天趋势、最近执行。
- 执行记录：按状态和执行 ID 查询、分页、创建执行、进入执行详情。
- 执行详情：节点图、节点记录、任务清单、产物引用、执行配置；支持刷新、服务端允许时恢复执行、提交停止请求。
- 人工审批：查看方案、变更范围和文件列表，显式批准或拒绝；拒绝必须填写原因。
- 模型调用审计和控制操作审计；运行详情通过 SSE 快照更新，并保留轮询兜底。

## 本地开发

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

默认 API 代理指向 http://127.0.0.1:8000。生产构建使用 npm run typecheck 和 npm run build。

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