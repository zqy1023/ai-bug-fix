# AI 异常日志自动修复工作流

项目按 `domain / application / workflow / agents / infrastructure / prompts` 分层。每个工作步骤是独立插件，输入、输出、执行函数和真实数据单步入口同目录维护。前端位于 `frontend/`，后端 API 位于 `workbench/api.py`，服务入口为根目录 `server.py`。

仓库文档统一收敛在本文件；`app/`、`app/agents/`、`app/workflow/steps/`、`frontend/`、`workbench/` 不再单独维护 README。

## 目录

- [主流程](#主流程)
- [代码结构](#代码结构)
- [工作步骤](#工作步骤)
- [Agent 子图](#agent-子图)
- [工作台后端](#工作台后端)
- [前端](#前端)
- [本地启动](#本地启动)
- [真实数据单步调试](#真实数据单步调试)
- [Docker 部署](#docker-部署)
- [延伸文档](#延伸文档)

## 主流程

```text
run 级（一次执行跑一遍）:
discover_and_normalize（discover_logs -> normalize_error -> deduplicate_error）
  -> identify_carrier
  -> task_dispatch（串行游标，每次只放行一个任务）
  -> 任务级子图（每个任务按 tasks 顺序串行执行）:
     prepare_context
       （prepare_source_snapshot -> repair_branch_preflight -> prepare_workspace -> prepare_evidence）
     -> locate_agent
     -> claim_repair_slot
     -> repair_agent
     -> validate_changes（check_changes -> verify_issue）
     -> run_regression
     -> review_agent
     -> request_approval
     -> prepare_delivery
     -> finalize_execution
  -> task_collect（记下结论，推进游标）-> 回到 task_dispatch
```

任务之间严格串行：前一个任务走到 `finalize_execution` 并记完汇总后，游标才推进到下一个。任务子图持有自己的状态（`WorkflowState`），父图只保留 run 级字段与结果汇总（`RunState`），所以 `changed_files`、`repair`、`locate` 这类任务局部通道不会跨任务累积。

旧框架判断只看 `gathers/<Carrier>/main.py`：存在则按新框架处理；不存在且 `spiders/<Carrier>/` 存在则在证据预检后转人工，不调用 Locate 或 Repair。缺少两类实现时步骤明确失败。

## 代码结构

```text
app/
├── domain/                  models.py, errors.py, policies.py, decisions.py
├── application/             context.py, contracts.py, services.py, audit.py
├── workflow/
│   ├── graph.py             LangGraph run 级主图 + 任务子图
│   ├── routes.py            门禁路由
│   ├── state.py             RunState（父图）与 WorkflowState（任务子图）
│   ├── registry.py          步骤与 Agent 注册
│   ├── runner.py            单步骤记录执行器
│   └── steps/<step>/        SPEC（__init__.py）、models、node、__main__
├── agents/                  common.py, policy.py, locate/, repair/, review/
├── infrastructure/          es/, git/, filesystem/, claude/, commands/, records/
├── prompts/                 common.md, locate.md, repair.md, review.md, loader.py, renderer.py
└── entrypoint.py            整图入口
```

每个 `workflow/steps/<step>/` 只包含该步骤的契约、执行器和 CLI。运行图直接连接这些插件，不使用隐藏的组合步骤。单步 CLI 只接受真实 `run_id`，任务级步骤还需 `task_id`；输入只从已通过的前置记录和运行 artifact 重建，不存在 fixture、stub 或伪造调试 ID。

## 工作步骤

目录名、`SPEC.name`、步骤记录名与调试模块名必须一致。原子步骤继续独立注册、独立记录、独立调试；LangGraph 图只调度业务阶段，组合阶段内部按顺序调用原子步骤，失败、跳过、人工介入或取消后立即停止后续子步骤。

### 工作流主节点

| 主节点 | 内部执行 / 职责 | 通过后的下一阶段 |
|---|---|---|
| `discover_and_normalize` | `discover_logs → normalize_error → deduplicate_error` | `identify_carrier` |
| `identify_carrier` | 按日志原始身份字段创建去重任务清单 | 串行任务分发 |
| `prepare_context` | `prepare_source_snapshot → repair_branch_preflight → prepare_workspace → prepare_evidence` | `locate_agent` |
| `locate_agent` | 只读定位；仅证据支持的 `LOCATED` 可继续 | `claim_repair_slot` |
| `claim_repair_slot` | 原子领取本轮修复资格 | `repair_agent` |
| `repair_agent` | 仅修改授权范围内的目标 carrier | `validate_changes` |
| `validate_changes` | `check_changes → verify_issue`，任一步失败即停止 | `run_regression` |
| `run_regression` | 在本地准生产 Git 环境执行回归，不提交 | `review_agent`（agent 模式）或直接交付分支（human 模式，跳过 AI 审查） |
| `review_agent` | 只读审查真实 Diff、门禁、验证和回归报告 | 交付、回修或人工处理 |
| `request_approval` | 交付方案人工决策；拒绝进入有上限的回修循环 | 收尾 |
| `prepare_delivery` | 创建修复分支并按配置提交/推送 | `finalize_execution` |
| `finalize_execution` | 生成终态报告并收尾 | 结束 |

任务子图保留 10 个业务主节点；`retry_prepare` 是内部控制节点，不计入节点数。run 级主图只保留日志准备阶段和 `identify_carrier` 两个节点。10 个任务主节点保留了 Agent、安全隔离、资格占用、回归、审批、交付等必须独立审计的边界。

### 原子步骤

| 原子步骤 | 层级 | 输出责任 |
|---|---|---|
| `discover_logs` | run | 保存实际日志查询条件 |
| `normalize_error` | run | 校验身份字段、拉取并规范化日志，写入代表问题产物 |
| `deduplicate_error` | run | 过滤重复与忽略项 |
| `identify_carrier` | run | 按 `(biztype, carrier)` 创建任务，并保留问题指纹 |
| `prepare_source_snapshot` | task | 保存目标源码基线及哈希产物 |
| `repair_branch_preflight` | task | 只读跳过已有未合并修复分支，不领取资格 |
| `prepare_evidence` | task | 校验框架入口并准备有限源码证据 |
| `claim_repair_slot` | task | 定位通过后原子领取本轮资格 |
| `prepare_workspace` | task | 共享本轮 Git 干净树，只复制目标 carrier 的新旧框架目录 |
| `check_changes` | task | 校验路径、范围、AST、敏感信息并保存完整 Diff |
| `verify_issue` | task | 验证原始问题是否修复 |
| `run_regression` | task | 执行配置的准生产回归 |
| `request_approval` | task | 保存人工决策 |
| `prepare_delivery` | task | 生成修复分支与交付结果 |
| `finalize_execution` | task | 汇总该任务的步骤记录、状态和产物 |

Locate、Repair、Review Agent 仍分别是独立 Agent 子图，不与确定性步骤合并。

单步入口：`python -m app.workflow.steps.<step> --run-id <real-run-id> [--task-id <task-id>]`。只读取真实记录和 artifact；不存在样例输入或替代数据路径。

### 终态语义

`status` 记录节点执行状态，`result` 记录业务结论。定位/审查返回 `MANUAL` 或证据不足时，`finalize_execution` 必须将最终报告写为 `MANUAL`，不能因节点自身 succeeded 而误记为 FAILED 或 SUCCESS。人工介入不等于审批；审批只处理存在明确方案且处于 interrupt 的任务。

human 模式（人工审查）与 agent 模式的差异只在审查环节：回归通过后同样交付修复分支并进入交付审批，由人工审查分支内容。终态按真实交付记录推导——没有真实提交（远端分支或源仓库本地分支可查）时不会记为 `SUCCESS`。

## Agent 子图

每个 Agent 子图独立维护状态、输入输出协议和 provider；主图只接收各自 `graph.build` 编译后的图。

| Agent | 图节点 | 权限 / 职责 |
|---|---|---|
| `locate` | `locate_agent` | 只读定位证据和候选文件 |
| `repair` | `repair_agent` | 可写，`write_root` 仅限目标 carrier |
| `review` | `review_agent` | 只读审查真实 diff、门禁和回归结果 |

修复对外只留 `passed` / `result` / `analysis` / `summary` / `change_plan` / `blocked_reason` / `scope_escalation_required`。审查对外只留 `verdict` / `passed` / `summary` / `findings` / `required_changes` / `change_plan`。

`change_plan` 是两者的正式分析通道：根因确实不在目标 carrier 时给出方案，`scope=project` 的方案需要人工确认；`scope=carrier` 且已就地改完时 `edits` 为空。

Agent 子图的 `State` 使用 TypedDict：**新增字段必须显式声明**，否则会被 LangGraph 静默丢弃（`RepairState.passed`、`ReviewState.change_plan` 都踩过这个坑）。

配置在各自目录的 `config.json`，环境变量 `AI_REPAIR_AGENT_<ROLE>_*` 覆盖。Repair 和 Review provider 仅提供 `claude_session`、`claude_query` 与显式转人工的 `manual` 通道。

所有 Agent 只通过 `app.prompts.PromptRenderer` 组装提示词。Prompt 文件固定为公共 `common.md` 以及每个职责一份角色文件（`locate.md`、`repair.md`、`review.md`）；服务名、carrier、证据、预算和权限范围通过强类型上下文传递，不按项目或 carrier 复制提示词。

### 性能预算与调用审计

每个 Agent 独立配置 `max_turns`、`max_tool_calls`、`max_context_chars`、`max_candidate_files` 与 `timeout_seconds`。定位阶段应优先使用确定性候选文件与符号证据；修复阶段只处理目标 carrier；审查阶段只读取真实 diff、门禁、验证和回归结果。重复的同参数工具调用会被 hook 拦截。审计只保留公开助手文本、工具调用/结果与耗时，不记录隐藏推理链。

## 工作台后端

唯一正式前端位于 `frontend/`，后端使用 FastAPI，执行引擎复用仓库现有 LangGraph 主图、步骤插件、RecordStore 与 checkpoint。已退役并删除的 `workbench/ui/` 不再作为启动或构建入口；旧 `/api` 启动入口复用 v1 的预创建、后台失败报告和生命周期锁。

工作台能力：

- 运行创建、列表、详情、配置与任务进度。
- 节点时间线及输入、输出、错误和产物引用。
- Provider 请求级模型调用审计：模型、Prompt 哈希、耗时、Token 用量（供应商未返回时为空）、工具调用统计与错误类型。
- 运行状态快照 SSE 和控制操作审计查询。
- 审批队列与 LangGraph checkpoint 恢复。
- 任务板投影（`/runs/{run_id}/board`）与单步骤详情、关联模型调用、脱敏上下文检查器。
- 单步骤重跑（`/runs/{run_id}/tasks/{task_id}/steps/{step_name}/run`）：仅在非活跃执行上允许，写步骤必须显式确认。
- 产物读取限制在当前运行/任务目录，限制最大读取大小。

工作台不提供知识案例或人工介入转派 API；人工决策只针对交付后的 `request_approval` 中断。旧 `apply_plan` 入口已退役，变更方案通过工作流的 `change_plan` 契约流转。

模型调用审计文件写入 `<运行时目录>/runs/<run_id>/model_calls.jsonl`。历史运行不会补造缺失的模型调用记录。Token 等用量字段以 Provider 实际返回为准。

提示词快照写入 `tasks/<task_id>/audit/prompts/<model_call_id>.json`，包含公共规则、角色提示词、任务上下文与字符数。快照由 `workbench_prompt_snapshot` 开关控制（默认开启），写入前按 `redact_output` 掩码凭据、把工作区绝对路径归一为项目相对路径，并按单段/总量上限截断；因此运行目录应视为包含业务上下文的高敏感数据，必须限制访问权限。

运行与安全边界：

- `server.py` 监听 `0.0.0.0:8000`。远程部署必须设置 `WORKBENCH_API_TOKEN`，并通过 HTTPS 反向代理和网络访问控制限制入口，禁止开放公网匿名访问。
- 运行记录目录应位于持久化磁盘；同时备份 `workflow-checkpoints.sqlite3`、`workbench-control.sqlite3` 与运行产物。
- 工作台不自动合并代码；代码交付仍遵守工作流门禁。
- 当前 SSE 发送的是基于持久化运行记录的状态快照，不等于逐 token 模型流。取消接口目前表达停止请求，当前节点可能完成后才响应。
- 本仓库尚未提供完整的企业 SSO/OIDC 与 RBAC；远程部署前必须补齐统一身份认证、角色授权、反向代理安全策略和端到端验收。
- MANUAL 任务作为终态记录展示，不进入交付审批队列。

## 前端

`frontend/` 包含基于 React、TypeScript、Vite、Ant Design、TanStack Query、React Flow 和 ECharts 的工作台 UI。运行态、审批项、步骤、模型调用、审计记录与产物内容均通过受控后端 API 获取。

信息架构：

- 执行阵列（`/runs`）：指标带、run 卡与任务热力条、状态/关键词筛选、分页和发起执行向导。
- Run 工作区（`/runs/:runId`）：run 级拓扑、按优先级分组的任务板、暂停/恢复/停止、只读 checkpoint 时间线与控制审计。
- Task 工作区（`/runs/:runId/tasks/:taskId`）：任务头、真实子图节点轨（组合阶段可展开）、步骤输入/输出/产物/错误、Agent 追踪与脱敏上下文检查器。
- Agent 追踪：提示词分段（版本、hash、字符数）、工具调用时间线与模型输出/Token 统计，按 attempt 切换。
- 交付审批（`/approvals`）：左队列右审阅；驳回必须填写理由并二次确认，批准保留分支供人工合并。

设计和状态原则：

1. 顶部为 56px 固定导航（执行阵列、交付审批角标、服务健康），底部固定状态条展示服务、运行目录与待审批数量。
2. 任务是最小观察单元：Run 页只承载任务卡与状态，步骤 I/O 与单步操作全部收敛到 Task 页。
3. 运行状态和业务结论分开展示。等待、失败、拒绝、证据不足、交付等状态不能混为一类。
4. 前端只展示后端授权的数据；产物通过受控 API 获取，不直接读取磁盘、Git 仓库、steps.json 或 LangGraph checkpoint。
5. 人工决策入口收敛到交付后的最终审批；`request_approval` 不提供单步执行入口。
6. 单步执行是写操作：必须持运行锁、写入控制审计，写步骤需要显式确认；活跃执行由服务端拒绝并返回 409。
7. 令牌只保存在当前页面内存中。按钮状态不替代服务端的权限、状态机、输入校验、幂等和审计。

API 约定：所有控制面请求通过 `src/lib/api.ts` 发出，路径以 `/api/v1` 为前缀；健康检查使用 `/api/health`。类型集中于 `src/lib/types.ts`。前端不直接访问数据库、Git 或本地文件系统。

## 本地启动

后端（仓库根目录）：

```powershell
python -m pip install -r requirements.txt
python server.py
```

前端（另开终端）：

```powershell
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

访问 Vite 输出的地址，默认 `http://127.0.0.1:5173`。开发代理默认指向 `http://127.0.0.1:8000`，可通过 `WORKBENCH_API_ORIGIN` 覆盖。`VITE_WORKBENCH_DEMO_MODE` 仅供本地演示，真实部署必须保持关闭。API 文档：`http://127.0.0.1:8000/docs`。

生产构建：

```powershell
cd frontend
npm run typecheck
npm run build
```

前端验证工作流会在推送和 PR 时执行类型检查与生产构建。远程构建不能替代真实后端环境下的端到端验证；部署前仍需验证审批状态切换、刷新与断线恢复、产物授权和运行控制。

## 真实数据单步调试

单步命令必须使用整图执行产生的真实 `run_id` 和任务 `task_id`。步骤从 `runtime/runs/<run_id>/steps.json` 与 artifact 重建输入，不接收 fixture、stub 或手写问题数据；缺少通过的前置记录时直接拒绝。

```powershell
.venv\Scripts\python.exe -m app.workflow.steps.normalize_error --run-id <real-run-id>
.venv\Scripts\python.exe -m app.workflow.steps.prepare_evidence --run-id <real-run-id> --task-id <task-id>
.venv\Scripts\python.exe -m app.agents.repair --run-id <real-run-id> --task-id <task-id> --confirm-write
```

需要写入隔离区、执行配置命令或进行 Git 交付的单步，要求显式指定 `--confirm-write`。完整工作流会生成新的 run ID：

```powershell
.venv\Scripts\python.exe -m app.entrypoint --run-id <run-id>
```

运行记录目录通过 `AI_REPAIR_RUNTIME_DIR` 配置，未设置时默认是项目根目录 `runtime/`；Docker 部署把持久化卷挂载到 `/app/runtime`，运行记录目录也使用 `/app/runtime`。

`runtime/workspaces` 是可重建缓存：`shared/<run>` 是本轮只读共享树，`ai/<run>-<task>` 是任务隔离区，`delivery/<branch>` 与 `regression/<task>` 是临时 Git 克隆。服务启动时会按 `AI_REPAIR_WORKSPACE_RETENTION_DAYS`（默认 7 天，仅清理已写终态报告的执行）回收 `shared`/`ai`，按 `AI_REPAIR_DELIVERY_WORKSPACE_TTL_HOURS`（默认 24 小时）回收 `delivery`/`regression`；设置 `AI_REPAIR_WORKSPACE_GC_ENABLED=false` 可关闭。审计事实保存在 `runtime/runs`，不参与回收。

### 源仓库模式

- **URL 模式（推荐）**：只配置 `AI_REPAIR_SOURCE_URL`（以及私有仓库的 `AI_REPAIR_GIT_USERNAME` / `AI_REPAIR_GIT_PASSWORD`）。系统把仓库克隆到 `<运行时目录>/source-repo`，每次执行工作流前执行 `fetch --prune` 并对齐远端默认分支，因此分支预检看到的是远端最新状态。凭据只注入 Git 子进程环境，不写入镜像的 `.git/config`。
- **本地模式**：配置 `AI_REPAIR_SOURCE_REPO` 指向本地 Git 仓库，适用于离线开发。该模式不会自动 fetch，分支预检只反映本地仓库已有的远端跟踪引用。

两种模式都通过 `AI_REPAIR_PROJECT_SUBDIR` 指定 `gathers/`、`spiders/` 所在的业务目录。

修复分支固定为 `ai-bug-tasks/<biz_type>/<carrier>-<问题指纹>`。`biz_type` 直接作为一级目录（允许中文），同一业务类型的修复分支收敛在同一目录，不再因为中文被清洗成 `item`。

URL 模式的镜像是**完整 Git 仓库**（引用、分支、对象都在，分支预检和回归/交付克隆都依赖它），但工作树用 `git sparse-checkout` 只物化 `AI_REPAIR_PROJECT_SUBDIR`。因此 `<运行时目录>/source-repo` 下只会看到 `.git` 和该业务目录，不会再把整仓库检出到磁盘；共享源码树同样用稀疏克隆创建。

## Docker 部署

生产容器入口是根目录 `docker-compose.yml`，前端由 Nginx 同源代理到 FastAPI。

1. 将根目录 `.env.example` 复制为 `.env`，填写所有真实连接参数与随机生成的 `WORKBENCH_API_TOKEN`。
2. 配置 `AI_REPAIR_SOURCE_URL` 及私有仓库凭据；设置 `AI_REPAIR_RUNTIME_HOST_PATH` 为宿主机持久化运行数据目录。启动前创建该目录，并确认 `APP_UID`/`APP_GID` 对应用户可以写入。
3. 容器只需挂载运行数据到 `/app/runtime`；源仓库在运行时从远程克隆，容器必须能访问 Git 远程地址。`AI_REPAIR_RUNTIME_DIR` 在 compose 中固定为 `/app/runtime`。
4. 构建机必须能够访问 `requirements.txt` 中声明的内部 `yq_common` 包地址。

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f api frontend
```

前端默认监听 `http://localhost:8080`。API 不直接映射宿主机端口；远程部署必须在 HTTPS 反向代理后访问。容器需要 Git 远程访问权限，必须确认 Git 身份、分支策略和凭据可用。默认不自动推送，禁止自动合并。不要将 `.env`、密钥或运行数据打入镜像。

## 延伸文档

- [工作台运行手册](docs/WORKBENCH_OPERATIONS.md)：部署、权限、验收门槛。
- [工作流逻辑审计](docs/WORKFLOW_AUDIT.md)：分支、终态与隔离不变量。
- [模型与配置字段参考](docs/MODEL_AND_CONFIG_REFERENCE.md)：Agent 模型、预算与配置字段。
- [运行产物与持久化说明](docs/RUNTIME_ARTIFACTS.md)：JSON、JSONL、SQLite 的审计、恢复与备份策略。
- `docs/enterprise-workbench-upgrade.md`、`docs/reliability-hardening.md`：历史设计与可靠性改造记录。
# 日志查询范围

工作台按“最近 N 小时”的滚动时间窗口查询日志，默认最近 24 小时，
使用 `log_hours` / `LOG_PULL_HOURS` 配置。新建执行默认 `log_limit=0`，表示窗口内全量拉取，
没有 1,000 / 10,000 条的隐藏截断，也没有扫描总时长截断；单次 ES 请求仍保留网络超时。
前端不再显示日志条数限制输入，读取阶段会展示已扫描、可解析和独立问题计数。
