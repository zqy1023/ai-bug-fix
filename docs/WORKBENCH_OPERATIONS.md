# 工作流企业级工作台运行手册

## 服务拓扑

```text
Browser -> Nginx (same-origin /api proxy) -> FastAPI Workbench API
                                      -> RecordStore (steps.json + artifacts)
                                      -> LangGraph SQLite checkpoints
                                      -> SQLite execution eligibility / control audit
                                      -> existing run_workflow / approval resume
```

## 运行数据契约

- Run：从 `runtime/runs/<run_id>/steps.json` 的 run 级步骤与任务集合派生。
- Task：`steps.json` 的 `tasks` 槽位、任务级步骤、`reports/final.json`。
- Step：执行状态 `status` 与业务结论 `result` 分开记录，另含耗时、尝试次数、输入/输出、错误和产物引用。
- Artifact：只能从对应 run/task 记录目录读取；路径必须保持在目录根内，响应上限 2 MiB。
- Checkpoint：以 `thread_id=run_id` 关联 LangGraph SQLite 检查点；审批恢复必须复用同一 thread_id。
- Eligibility：SQLite 唯一键 `run_id|biztype|carrier` 保证同一轮同一运输商只领取一次资格。
- MANUAL：人工介入终态，不是成功也不是待审批方案。待审批列表只包含实际挂起的审批；人工介入列表显示定位/审查的 MANUAL 结论。

## 本地启动

1. 复制 `.env.example` 为 `.env`，填写 ES、源仓库、验证命令与 Agent 网关配置。
2. 本地运行 API：`uvicorn workbench.api:app --host 127.0.0.1 --port 8000`。
3. 在 `frontend/` 运行 `npm install`、`npm run dev`。
4. 通过 `GET /api/health` 检查后端配置，通过 `GET /api/docs` 查看 API 契约。
5. 先用测试仓库验证完整链路，禁止直接用生产源仓库做首轮验证。

## Docker Compose 部署

1. 将根目录 `.env.example` 复制为 `.env`，填写所有真实连接参数与随机生成的 `WORKBENCH_API_TOKEN`。
2. 设置 `AI_REPAIR_SOURCE_REPO_HOST_PATH` 为宿主机 Git 仓库路径，设置 `AI_REPAIR_RUNTIME_HOST_PATH` 为宿主机持久化运行数据目录。启动前创建该目录，并确认 `APP_UID`/`APP_GID` 对应用户可以写入源仓库和运行目录。
3. 容器内源仓库固定挂载到 `/workspace/source`，运行数据固定挂载到 `/app/runtime`。API 的 `AI_REPAIR_SOURCE_REPO` 和 `AI_REPAIR_RUNTIME_DIR` 已覆盖为容器路径。
4. 构建机必须能够访问 `requirements.txt` 中声明的内部 `yq_common` 包地址。
5. 执行：

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f api frontend
```

6. 前端默认监听 `http://localhost:8080`。API 不直接映射宿主机端口；远程部署必须在 HTTPS 反向代理后访问。
7. 源仓库挂载为读写模式，必须确认 Git 身份、分支策略和凭据可用。默认不自动推送，禁止自动合并。不要将 `.env`、密钥、源仓库或运行数据打入镜像。

## 审批与权限

批准或拒绝是业务决定，应先查看真实 diff、验证/回归/审查结果。工作台使用 Token 级入口，不内置企业 SSO/RBAC；多人共享部署必须通过受控网关限制审批人员，并将操作审计写入集中日志。禁止开放公网匿名访问。

## 验收门槛

- [ ] `python -m compileall -q app workbench config tests` 成功。
- [ ] `pytest -q` 通过。
- [ ] `cd frontend && npm install && npm run typecheck && npm run build` 成功。
- [ ] `docker compose config` 与 `docker compose build` 成功。
- [ ] 两个独立进程同时领取资格时只有一个成功。
- [ ] 服务重启后可以恢复等待审批的 LangGraph thread。
- [ ] 批准/拒绝只作用于对应 run/task，不重复执行 Git commit/push。
- [ ] 任务工作区互相隔离；产物 API 无路径穿越。
- [ ] MANUAL 任务最终报告为 MANUAL，审批中心与人工介入队列区分正确。
- [ ] CI、回归、故障注入和备份恢复验证有可追溯结果。

