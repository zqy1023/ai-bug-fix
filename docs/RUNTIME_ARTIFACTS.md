# 运行产物与持久化文件说明

本文中的 `runtime_dir` 指部署配置的运行数据根目录；单次执行目录为 `runtime/runs/<run_id>/`。不同节点按实际执行路径生成产物，未执行的节点不会保证生成对应文件。

## 目录结构

```text
runtime/
├── workflow-checkpoints.sqlite3
├── eligibility.sqlite3
├── eligibility.json                    # 旧版本迁移来源
├── workbench-control.sqlite3
└── runs/<run_id>/
    ├── run.json
    ├── steps.json
    ├── model_calls.jsonl
    ├── reports/run_failure.json        # 仅后台异常时生成
    └── tasks/<task_id>/
        ├── agents/
        │   ├── locate_agent.json
        │   ├── repair_agent.json
        │   └── review_agent.json
        ├── audit/agent_events.jsonl
        ├── metrics/agent.jsonl
        ├── reports/final.json
        └── ...                         # 证据、diff、检查、回归、交付等步骤产物
```

## 文件说明

| 路径 | 用途 | 生命周期 |
|---|---|---|
| `runs/<run_id>/run.json` | 本轮启动输入、参数和配置快照 | 启动时创建，供审计实际输入 |
| `runs/<run_id>/steps.json` | Run 与 task 的节点状态、业务结论、时间、耗时、错误摘要、输入输出投影和产物引用 | 随节点记录更新；不存大段事件流 |
| `runs/<run_id>/model_calls.jsonl` | 每次模型调用的 ID、Agent 角色、模型、Prompt 版本/hash、耗时、用量、状态、事件文件路径和事件数 | 每次调用追加一行；不重复保存完整事件数组 |
| `runs/<run_id>/tasks/<task_id>/agents/*.json` | locate、repair、review Agent 的结构化结论 | 对应 Agent 节点完成时生成 |
| `runs/<run_id>/tasks/<task_id>/audit/agent_events.jsonl` | 过滤后的工具调用、工具结果和最终公开输出；用 `model_call_id` 关联调用元数据 | 每个事件一行，按任务追加 |
| `runs/<run_id>/tasks/<task_id>/metrics/agent.jsonl` | Agent 调用耗时、Token 用量、工具次数和状态 | 每次调用追加一行 |
| `runs/<run_id>/tasks/<task_id>/reports/final.json` | 任务最终结论、耗时、Token 汇总和完成步骤 | 任务收尾时生成；取消收尾保留 `CANCELLED` |
| `runs/<run_id>/reports/run_failure.json` | 后台执行线程的未捕获异常摘要 | 仅发生此类异常时生成 |
| `runs/<run_id>/tasks/<task_id>/**` 中的其他 JSON | 证据、源码 diff、验证、回归和交付等节点产物 | 按节点声明的相对路径生成，`steps.json` 保存引用 |
| `runtime/workbench-control.sqlite3` | 启动幂等信息、暂停/取消请求和控制操作审计 | 工作台控制 API 更新 |
| `runtime/eligibility.sqlite3` | SQLite 唯一约束，确保同一 Run 中同一承运商问题只领取一次修复资格 | 领取资格时事务写入；旧版 `eligibility.json` 仅用于迁移 |
| `runtime/workflow-checkpoints.sqlite3` | LangGraph 持久化状态和 interrupt/checkpoint | 图执行过程中更新，支持审批/暂停后恢复 |

`JSON` 是结构化快照或最终结果；`JSONL` 是追加式日志，每行都是独立 JSON 对象，不能把整个文件直接作为单个 JSON 文档解析。SQLite 用于控制状态、修复资格和 LangGraph 检查点，不能代替审计文件。

## Agent 事件过滤与内存控制

- 事件按 `run_id + task_id + model_call_id` 关联，持久化到 task 的 `audit/agent_events.jsonl`。
- 只保留工具调用摘要、工具结果摘要和最终公开输出；模型中间分析文本不入库。
- 工具输入只保留路径、查询、模式等定位字段，不保存 Prompt、文件正文、编辑前后文或命令正文。
- 文本裁剪并对常见 Authorization、Token、Password、Secret 字段做遮蔽；这属于纵深防护，不替代工作区脱敏。
- 流式调用中途异常时，SDK 层将已收集事件附加到异常，审计层先保存事件，再写失败元数据。
- 调试 Hook 只写摘要，单文件上限 5 MiB，超过后滚动保留一份；关闭调试 Hook 不关闭 task 级审计。
- 历史调用如果只有模型元数据且无事件文件，前端如实显示无持久化轨迹，不伪造历史事件。

## 暂停、继续、取消

- 暂停请求先显示 `pause_requested`；到达安全节点边界并保存 LangGraph interrupt/checkpoint 后才显示 `paused`。
- 继续使用同一 `run_id/thread_id`；中断检查点使用 `Command(resume=None)` 恢复，未中断检查点按既有状态恢复。
- 取消正在执行的 Run 会在安全边界进入收尾；取消已暂停的 Run 会清除暂停标志并恢复检查点，确保最终报告落为 `CANCELLED`，而不是永远停在暂停态。
- 取消后不得继续回修、审批、回归或 Git 交付。Git 合并仍由人工完成。

## 备份与清理

1. 将整个 `runtime/` 作为持久化目录挂载，不要只挂载 `runs/` 而遗漏 SQLite 数据库。
2. 备份 SQLite 优先使用在线备份机制，或先停止服务；不要在数据库运行时只复制主数据库而忽略 `-wal`、`-shm` 文件。
3. 需要可恢复能力时，JSON/JSONL 和 checkpoint 应来自同一时间点；只恢复 JSON 无法恢复 LangGraph 执行位置。
4. 清理旧 Run 前，先归档 `run.json`、`steps.json`、模型调用/事件审计和最终报告，并确认 checkpoint 不再需要恢复。
5. 不要将 `runtime/`、SQLite 文件、事件日志、源仓库凭据或 `.env` 提交进 Git；Docker 构建上下文由 `.dockerignore` 排除这些内容。
