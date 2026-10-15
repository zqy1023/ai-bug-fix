# AI Repair Workbench

这是连接真实运行记录与 LangGraph 检查点的运维工作台，不是静态演示页面。前端使用 React + TypeScript + Vite，后端使用 FastAPI；运行、任务、节点输入输出、错误、产物和审批都读取项目真实 runtime 数据。

## 本地启动

在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt
uvicorn workbench.api:app --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
cd workbench/ui
npm install
npm run dev
```

访问 Vite 输出的本地地址（默认 http://127.0.0.1:5173）。前端代理 `/api` 到 `127.0.0.1:8000`。生产构建：

```powershell
cd workbench/ui
npm run build
```

API OpenAPI 文档：`http://127.0.0.1:8000/api/docs`。

## 认证与部署

- 默认只监听回环地址，适用于本机运维。
- 远程部署必须设置 `WORKBENCH_API_TOKEN`，通过 HTTPS 反向代理访问，并将 Token 配置到页面右上角的 API 认证中。
- 不要把工作台 API 直接暴露到公网。Token 仅保存于当前浏览器会话的 sessionStorage。
- 运行目录由现有 `AI_REPAIR_RUNTIME_DIR` 决定。请将其放在持久化磁盘，并备份运行记录、`workflow-checkpoints.sqlite3` 和 `eligibility.sqlite3`。
- API 仅允许读取运行目录中的记录/产物；不提供任意文件读取，也不会展示工作区中的密钥文件。
- 人工审批通过 LangGraph `Command(resume=...)` 恢复持久化 thread。批准前必须在任务详情查看验证、回归与审查证据。工作台不会自动合并分支。

## 能力

- 运行总览、任务成功/失败与待审批数量。
- 5 秒轮询执行列表与实时节点记录；可查看节点耗时、尝试次数、输入、输出和异常。
- 按运行轮次查看任务轨迹、Agent 与步骤产物。
- 审批队列，审批后恢复挂起工作流。
- 系统健康、关键配置状态和 API 文档。

## 生产化边界

该版本是项目内的可运行控制台基线，不替代企业统一身份认证、RBAC/审批人权限矩阵、审计日志集中化、告警路由、反向代理限流与部署编排。部署到共享环境前应补齐这些运维控制并完成集成验收。