# 工作流企业级工作台运行手册

## 服务拓扑

```text
Browser (React + TypeScript)
  -> Vite dev proxy / HTTPS reverse proxy
    -> FastAPI Workbench API
      -> RecordStore (steps.json + artifacts)
      -> LangGraph SQLite checkpoints
      -> SQLite execution eligibility
      -> existing run_workflow / approval resume
```

## 运行数据契约

- Run：由 `runtime/runs/<run_id>/steps.json` 的 run 级步骤与任务集合派生。
- Task：`steps.json` 的 `tasks` 槽位、任务级步骤、`reports/final.json`。
- Step：原始步骤记录，包含 status/result、duration、attempt、input/output、error 和 artifact refs。
- Artifact：只能从 run/task 的记录目录中读取，路径必须保持在对应根目录内，响应上限 2 MiB。
- Checkpoint：以 `thread_id=run_id` 关联 LangGraph SQLite 检查点；审批恢复必须复用同一 thread_id。
- Eligibility：SQLite 唯一键 `run_id|biztype|carrier` 保证同一轮次同一运输商只领取一次资格。

## 运维启动

1. 检查 `.env` 中的 ES、源码仓库、验证命令与 Agent 网关配置。
2. 运行 `uvicorn workbench.api:app --host 127.0.0.1 --port 8000`。
3. 运行前端 `cd workbench/ui && npm ci && npm run build`，开发阶段使用 `npm run dev`。
4. 通过 `GET /api/health` 检查配置，通过 `GET /api/docs` 查看 API 契约。
5. 使用测试 run 验证发现、隔离工作区、检查、验证、回归、审查、审批恢复和 Git 分支交付。
6. 上线后备份 runtime 目录；监控服务进程、磁盘、checkpoint SQLite、API 4xx/5xx 和运行失败率。

## 审批安全

批准或拒绝是不可逆的业务决策，应先查看完整差异与验证/回归/审查结果。该工作台只提供 Token 级入口，不内置企业 SSO/RBAC；多人共享部署必须通过受控网关限制审批人员，并将操作审计写入集中式日志。禁止开放公网匿名访问。

## 验收门槛

- [ ] 前端 `npm run build` 成功。
- [ ] Python 测试通过，且运行在目标 Windows/Linux 环境。
- [ ] 两个独立进程同时领取资格时只有一个成功。
- [ ] 服务重启后可以恢复等待审批的 LangGraph thread。
- [ ] 批准/拒绝只作用于对应 run/task，不重复执行 Git commit/push。
- [ ] 任务工作区互相隔离；产物 API 无路径穿越。
- [ ] CI、回归、故障注入和备份恢复验证有可追溯结果。
