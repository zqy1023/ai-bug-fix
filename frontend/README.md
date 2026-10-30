# 修复工作台前端

React 19 + Vite + TypeScript + React Query。样式使用 `src/styles/app.css` 的设计令牌与组件类，不依赖 Ant Design。

## 本地运行

```bash
cd frontend
pnpm install
pnpm dev
```

开发服务器默认 `http://127.0.0.1:5173`，`/api` 代理到 `WORKBENCH_API_ORIGIN`（默认 `http://127.0.0.1:8000`）。

生产构建：

```bash
pnpm build
```

构建产物 `dist/` 由 `python server.py` 直接托管，访问 `http://127.0.0.1:8000/`。

## 路由

| 路径 | 页面 |
|---|---|
| `/` | 概览：活跃批次、需要处理、最近批次、7 日趋势 |
| `/runs` | 批次列表、筛选、新建批次 |
| `/runs/:runId` | 批次详情：任务、流水线、Run 控制 |
| `/runs/:runId/records` | 批次记录：执行阶段、控制审计、恢复点 |
| `/runs/:runId/artifacts` | 批次产物（Run 级） |
| `/runs/:runId/tasks/:taskId` | 任务概览：结论、完整异常原文、根因、审查、交付 |
| `/runs/:runId/tasks/:taskId/change` | 改动：diff、改动摘要、审查结论 |
| `/runs/:runId/tasks/:taskId/audit/steps` | 审计：步骤时间线、步骤抽屉、重跑/继续 |
| `/runs/:runId/tasks/:taskId/audit/agent` | 审计：Agent 对话、思考、工具调用 |
| `/runs/:runId/tasks/:taskId/audit/prompt` | 审计：Prompt 快照 |
| `/runs/:runId/tasks/:taskId/audit/artifacts` | 任务产物（Task 级） |
| `/settings` | 运行默认值、模型目录、Agent 策略、系统健康 |

## 数据约定

- 类型契约在 `src/lib/types.ts`，后端契约由 `workflow/engine/handoff.py` 生成到 `src/lib/workflow.generated.ts`。
- 任务异常原文：`task.problem.message` 可能被步骤记录裁剪；`useProblemLog` 检测 `已截断 / 原长 N 字符` 后回读 `artifacts/normalized_records.json`，页面展示完整 message。
- 任务产物使用任务详情里的 `artifacts[]`；批次产物使用 `/runs/:runId/artifacts`，两者不混用。
- Agent 事件与 Prompt 快照不在 `artifacts[]`，分别走 `agent-calls` 接口。
- 任务重试只有一个 `POST /tasks/:taskId/retry`：MANUAL / INSUFFICIENT_EVIDENCE / BLOCKED 会新建 Run，FAILED 在原 Run 重试；前端只展示一个动作，按状态切换文案。
- 人工审批已移除；不再有 `/approvals` 路由或审批按钮。

## 设置

- `GET /api/v1/run-config` 返回 Nacos 有效默认值、Nacos 目标（data_id / group）和当前覆盖。
- `PUT /api/v1/run-config` 直接读-改-写 Nacos JSON 配置，写入 `LOG_PULL_HOURS`、`LOG_PULL_LIMIT`、`AI_REPAIR_MAX_ATTEMPTS` 和 `AI_REPAIR_AGENT_POLICY` 中的默认模型。
- `DELETE /api/v1/run-config` 删除这些键，恢复环境默认。
- 不写本地文件；Nacos 未启用或写入失败时接口返回 503，设置页显示不可保存。
