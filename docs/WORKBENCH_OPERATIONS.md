# 工作流企业级工作台运行手册

## 服务拓扑

```text
Browser -> Nginx (same-origin /api proxy) -> FastAPI Workbench API
                                      -> RecordStore (steps.json + artifacts)
                                      -> LangGraph SQLite checkpoints
                                      -> SQLite control audit
                                      -> existing run_workflow / approval resume
```

## 运行数据契约

- Run：从 `runtime/runs/<run_id>/steps.json` 的 run 级步骤与任务集合派生。
- Task：`steps.json` 的 `tasks` 槽位、任务级步骤、`reports/final.json`。
- Step：执行状态 `status` 与业务结论 `result` 分开记录，另含耗时、尝试次数、输入/输出、错误和产物引用。
- Artifact：只能从对应 run/task 记录目录读取；路径必须保持在目录根内，响应上限 2 MiB。
- Checkpoint：以 `thread_id=run_id` 关联 LangGraph SQLite 检查点；暂停/继续/审批恢复必须复用同一 thread_id。
- Agent 调用：`model_calls.jsonl` 记录调用元数据、输出摘要与事件索引；`tasks/<task_id>/audit/agent_events.jsonl` 按任务追加过滤后的助手公开文本、工具调用和工具结果。隐藏思考块、完整 SDK 对象和 Prompt 不写入调试归档。
- 调试 hook：`AI_REPAIR_DEBUG_HOOK_LOG` 仅写过滤后的事件摘要，单文件上限 5 MiB，超过后滚动保留一份。
- MANUAL：人工介入终态，不是成功也不是待审批方案。审批列表只包含实际挂起的交付审批。

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
- [ ] 服务重启后可以恢复等待审批的 LangGraph thread。
- [ ] 批准/拒绝只作用于对应 run/task，不重复执行 Git commit/push。
- [ ] 任务工作区互相隔离；产物 API 无路径穿越。
- [ ] MANUAL 任务最终报告为 MANUAL，且不进入交付审批队列。
- [ ] CI、回归、故障注入和备份恢复验证有可追溯结果。


## 每次执行的 JSON 与 SQLite 文件说明

| 文件 | 作用 | 生成时机 / 保留原则 |
|---|---|---|
| `run.json` | 本轮启动参数与配置快照，用于真实输入重建 | 启动时创建，不覆盖 |
| `steps.json` | Run 级与任务级节点状态、业务结论、摘要、耗时、错误和产物引用 | 每个节点状态变化时原子更新；不存大段原始事件 |
| `tasks/<task_id>/reports/final.json` | 单个任务的最终业务结论、耗时、Token 汇总和完成步骤 | 任务收尾时生成；取消任务标记 CANCELLED |
| `reports/run_failure.json` | 图外后台异常的安全摘要 | 仅后台发生未捕获异常时生成 |
| `tasks/<task_id>/agents/*.json` | locate/repair/review Agent 的结果摘要和原始业务结论 | Agent 节点完成时生成；不包含隐藏思考流 |
| `tasks/<task_id>/metrics/agent.jsonl` | 每次 Agent 调用的 token 用量、耗时、工具次数和业务状态 | 每次 Agent 调用追加一行 |
| `tasks/<task_id>/audit/agent_events.jsonl` | 过滤后的工具调用、工具结果和最终公开输出，按调用 ID 关联 | 每次调用追加多行；不保存中间分析、Prompt 正文或编辑全文 |
| `tasks/<task_id>/**` 下的其他 JSON | 证据、diff、验证、回归和交付报告等节点产物 | 由步骤按声明的相对路径生成；steps.json 只保存引用 |
| `model_calls.jsonl` | 每次 Provider 调用的模型、角色、耗时、用量、输出摘要及事件索引 | 每次模型调用追加一行；旧记录缺失事件时不补造 |
| `workbench-control.sqlite3` | 启动幂等键、暂停/取消意图、控制操作审计 | 控制 API 首次调用时创建；必须持久化挂载 |
| `workflow-checkpoints.sqlite3` | runtime 根目录的 LangGraph 状态检查点，支持审批/暂停后继续 | 节点提交状态时更新；必须与同一 runtime 的 JSON/JSONL 一起备份 |

运行根目录下的 JSON 是可读审计记录，SQLite 用于控制状态和图检查点；二者不是互相替代关系。备份时应整体备份同一 runtime 目录，恢复后再验证 checkpoint 与 JSON 记录的 run_id 一致。
