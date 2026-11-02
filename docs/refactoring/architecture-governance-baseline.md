# 架构治理重构基线

> 基线分支：`main`
> 基线提交：`b4e598ca88c7455fcfdd4ce5705abd4fd7e247d0`
> 重构分支：`refactor/workflow-control-plane`
> 本文记录代码检查确认的事实与后续治理约束；未执行的测试不会标记为通过。

## 1. 当前已确认的结构

| 领域 | 当前实现位置 | 当前职责/观察 |
|---|---|---|
| 核心契约 | `core/contracts.py` | 同一文件包含 Step/Agent 插件契约、执行结果、TaskSnapshot、Handoff、AgentRequest 等多类模型 |
| 插件注册与执行 | `workflow/engine/runner.py` | 集中注册插件、校验拓扑、组装输入、校验输出、记录步骤 |
| 工作流图 | `workflow/engine/graph.py` | 负责 LangGraph 图构建、SQLite checkpoint、Run/Task 调度 |
| Run/Task 生命周期 | `infrastructure/storage/lifecycle.py` | 基于 RecordDatabase 保存生命周期状态 |
| 记录数据库 | `infrastructure/storage/database.py` | 每个 Run 的 `records.sqlite`，保存 state/events |
| 工作台控制库 | `server/control_store.py` | 全局 `workbench-control.sqlite3`，保存幂等键、暂停/取消标志和控制审计 |
| 控制信号读取 | `infrastructure/storage/run_control.py` | 工作流执行阶段读取暂停/取消标志；已改为控制库读取失败时 fail closed |
| 图 Checkpoint | `workflow/engine/graph.py` | 全局 runtime 下的 `workflow-checkpoints.sqlite3` |
| 配置 | `config/settings.py`、`config/nacos.py` | dataclass 默认值、环境变量和 Nacos 配置读取 |
| 前端契约 | `frontend/src/lib/types.ts`、`frontend/src/lib/api.ts` | 前端模型与 API 调用 |

## 2. 第一项已修复的问题

### 控制库读取异常被当作“没有控制请求”

文件：`infrastructure/storage/run_control.py`

原实现捕获 `sqlite3.Error` 后记录 warning 并返回 `(False, False)`。这会把“控制库不可读”与“用户没有请求暂停/取消”混为一谈，可能导致控制信号失效。

本分支改为：
- 控制库不存在：保持兼容，视为尚无持久化控制请求。
- 控制库存在且可读：返回真实控制标志。
- 控制库存在但读取失败：抛出 `ControlStoreUnavailable`，由步骤执行边界按失败处理，禁止静默放行。

新增测试覆盖数据库锁定/读取异常，以及控制库不存在两种情况。

## 3. SQLite 连接能力已开始统一\n\n新增 `infrastructure/storage/sqlite.py`，提供 `open_sqlite()` 统一连接创建、busy timeout、row factory 和可选 WAL 设置。记录库、工作台控制库、Checkpoint 和控制信号读取已改为复用该入口；各自的 Schema 和事务边界仍由存储所有者维护。新增 `tests/test_sqlite_connection.py` 覆盖连接参数和非法 journal mode。\n\n这一步只统一连接基础能力，不代表数据库 Schema、事务、数据所有权和迁移治理已经完成。\n\n## 4. 数据存储治理待办

当前至少存在三种 SQLite 用途，不能简单合并成一个数据库，也不能继续让模块自行管理相同表结构。

| 存储 | 当前位置 | 后续治理 |
|---|---|---|
| Run 记录库 | `runs/<run_id>/records.sqlite` | 明确 state/events 的所有权、索引和事务接口 |
| 工作台控制库 | `runtime/workbench-control.sqlite3` | 统一 schema 初始化、控制读写和审计入口 |
| LangGraph Checkpoint | `runtime/workflow-checkpoints.sqlite3` | 保留图恢复职责，不存放业务生命周期的第二份事实 |

治理原则：Checkpoint 只负责图恢复；Lifecycle 负责 Run/Task 操作状态；RecordStore 负责步骤审计与产物索引；控制库负责控制意图和幂等操作。投影层只读，不成为新的写入事实源。

## 5. 配置治理待办

已确认配置存在至少两层：
- `config/settings.py` 的 dataclass 默认值；
- `config/nacos.py` 的 Nacos/环境变量覆盖。

下一步需要逐项建立配置注册表，补充类型、范围、默认值、敏感级别、实际消费者、运行时快照和废弃状态。不能仅凭配置文件存在就认定配置有效，也不能在未确认消费者前删除配置。

## 6. 流程数据契约治理待办

`core/contracts.py` 已有 `StepSpec`、`AgentSpec`、`TaskSnapshot`、`StepOutcome`、`Handoff` 等契约；`workflow/engine/runner.py` 已有输入模型、输出模型及插件注册自检。

后续应在现有机制上增强，而不是另造一套平行 Schema：
1. 为每个插件登记输入/输出字段及 Artifact 输出。
2. 通过静态分析和运行记录统计字段消费者。
3. 区分路由状态、领域数据、临时上下文和大体积 Artifact。
4. 对无消费者字段先标记候选删除，完成调用方迁移和回归后再删除。
5. Agent 原始响应保留在 Artifact，State 只携带后续确实需要的结构化结果或引用。

## 7. 重构实施顺序

1. P0 执行安全：控制信号、执行所有权、取消/暂停边界。
2. 存储治理：统一数据库 schema/连接/事务入口，明确每类数据唯一写入者。
3. 契约治理：在现有插件注册体系上增加字段消费者和 Artifact 声明。
4. 配置治理：配置注册、严格校验、启动快照和敏感值脱敏。
5. 工作流去重：根据调用图与数据消费者合并/删除节点，不按名称猜测。
6. API/前端治理：从后端投影输出稳定、精简的视图模型。
7. 故障注入与端到端回归。

## 8. 验收约束

- 不引入新的业务数据库，除非扫描证明现有 SQLite 无法满足需求并得到明确决策。
- 不允许节点直接绕过领域服务写其他模块的数据库。
- 不允许无消费者字段继续膨胀核心 State。
- 不允许旧报告、旧 Diff 或旧审批引用新 Attempt 的数据。
- 不允许控制存储故障被解释为“未请求取消/暂停”。
- 不删除业务能力；所有节点合并、字段删除和表迁移必须有消费者证据及回归测试。
- 只有实际执行并取得结果的测试才标记为通过。
