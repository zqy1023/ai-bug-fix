# AI 异常日志自动修复工作流

当前工作流版本为 **v7**。后端入口为 `server.py`，API 位于 `workbench/api.py`，前端位于 `frontend/`。

## 主流程

```text
sync_source_repo
→ discover_and_normalize（discover_logs → normalize_error → deduplicate_error）
→ identify_carrier（biztype + carrier + 异常指纹建立任务）
→ 串行任务分发
  → prepare_context（repair_branch_preflight → prepare_source_snapshot → prepare_workspace → prepare_evidence）
  → locate_agent
  → define_acceptance → reproduce_baseline
  → claim_repair_slot → repair_agent
  → review_agent（内部执行：范围 → 语法 → 验证计划 → 原问题重放 → 真实请求 → 证据汇总 → 综合审查）
  → decide_validation
  → prepare_delivery（本地候选）→ request_approval → publish_delivery
  → finalize_execution
```

`review_agent` 是修复后的唯一审查入口；内部检查只记录在 `artifacts/review_agent/`，不再向主 `steps.json` 添加独立验证节点。`human` 模式仍从同一入口收集证据，但不调用审查模型，代码语义交由人工审查。Agent 模式不因单项请求失败直接判定修复失败。
定位不确定、旧框架、非 carrier 修改范围或修改前基线未复现时走 `prepare_analysis → prepare_delivery → finalize_execution`；分析报告附带未执行的建议验收标准，报告生成不提前释放资格或宣告任务完成。

任务有独立 checkpoint。任务执行串行；某个任务等待人工审批时，父图可继续处理后续任务。工作流不自动合并分支。

## 代码结构

| 模块 | 唯一职责 |
|---|---|
| `app/workflow/definitions.py` | 阶段组合、跳转和原子步骤依赖；前端阶段由后端投影生成 |
| `app/workflow/contracts.py` | 步骤结果、验收断言、证据身份、分层状态快照和最终结论 |
| `app/workflow/handoff.py` | 状态归属校验、下游失效、完整交接产物写入 |
| `app/workflow/acceptance.py` | 修改前验收与完整基线身份核对 |
| `app/workflow/validation_executor.py` | 隔离的基线与修复代码重放 |
| `app/workflow/validation.py` | 补丁验证计划、断言汇总和统一决策 |
| `app/workflow/runner.py` | 输入输出校验、执行状态、步骤记录 |
| `app/workflow/agent_adapter.py` | 三个 Agent 子图的类型化输入与正式结果适配 |
| `app/workflow/wiring.py` | 服务装配、图构建和 checkpoint 恢复 |
| `app/application/step_runner.py` | 单步执行及完整状态恢复 |

插件 `SPEC` 只声明输入输出模型、执行器和输出适配，不再分别声明下一步和依赖。依赖来自 `STEP_DEPENDENCIES`；完整执行和单步续跑共用图定义。

## 工作步骤

v7 有 17 个主流程确定性步骤插件，另有 Locate、Repair、Review 三个 Agent。

| 步骤 | 正式产出 / 判断依据 |
|---|---|
| `discover_logs` | 日志查询条件 |
| `normalize_error` | 规范化、去重后的原始问题产物及计数 |
| `deduplicate_error` | 去重与忽略统计 |
| `identify_carrier` | 完整任务清单，保留同 carrier 的不同异常 |
| `repair_branch_preflight` | 已有修复分支检查，在源码快照之前执行 |
| `prepare_source_snapshot` | 修复前源码、manifest、框架和基线路径 |
| `prepare_workspace` | 隔离工作区 |
| `prepare_evidence` | 候选文件、源码符号和框架证据 |
| `locate_agent` | 根因、修改范围、证据和缺口 |
| `define_acceptance` | 固定的 `AcceptanceContract` |
| `reproduce_baseline` | `BaselineReproduction`，不运行修复后代码 |
| `claim_repair_slot` | 修复资格 |
| `repair_agent` | 修改说明和声明的改动清单；输入包含验收契约 |
| `review_agent` | 修复后代码审查 Agent：语法、业务修复、影响范围及环境/官网状态说明 |
| `decide_validation` | 重试、回修、准备交付或阻断 |
| `prepare_analysis` | 分析报告，不产生任务终态 |
| `prepare_delivery` | 本地候选提交，修复任务禁止在此推送 |
| `request_approval` | 与候选、实际补丁及证据绑定的人工决定 |
| `publish_delivery` | 正式交付同一候选提交，按配置推送 |
| `finalize_execution` | 唯一终态报告及 `TaskResolution` |

## 验证与统一决策

### 步骤结果

每步正式结果为：

```text
execution: status + error(code, message, retryable)
assessment: outcome + code + summary + findings
output: 该步骤输出模型校验后的完整数据
artifact_paths: 正式证据引用
```

- 执行状态：`succeeded / failed / waiting / cancelled`。
- 业务结论：`passed / failed / inconclusive / not_applicable`。
- 验证器正常运行发现代码错误：执行成功、业务失败；启动失败或超时：执行失败、结论无法确认。
- 原有步骤 `status/result` 保留为列表及历史记录投影，不作为单步恢复的数据源。

### 状态交接

`workflow/state.json` 保存最后一次完整交接，`workflow/outputs/<step>.json` 保存该步骤完整结果。
状态按 `identity / policy / control / context / artifacts / run / outcomes` 分层。LangGraph 的扁平通道是执行适配；持久化与恢复使用 `TaskSnapshot`。

步骤只能更新所属产物。修复 Agent 无权改写基线或验收。重新定位使验收及下游失效；修改补丁使检查、验证、审查、候选和审批失效。恢复读取完整交接文件，禁止从截断的 `steps.json` 反向拼状态。

### 验收与验证

修改前固定验收契约，以**完整 baseline manifest**计算基线身份，不以改动文件子集计算。当前平台支持基线异常匹配、正文送达、修复后框架语义比较和真实请求四项断言。
验收契约与修改后执行计划分开：补丁变化只重建执行计划；验收内容、原始问题或基线变化分别给出错误码。

修复后代码审查 Agent 按语法检查、业务修复检查和影响范围检查三个维度综合判断。重放与真实请求保留为执行证据；请求失败需区分代码缺陷、环境网络/代理/依赖故障与官网状态，不自动触发回修。三项审查均通过、证据有效且覆盖充分后才能准备候选；证据不足则不进入修复，转分析报告交付并由人工验证，确认代码缺陷才回修。人工模式仍要求原问题重放和真实请求通过。真实请求保持原始请求参数和代理渠道，调用 `server_dev.main`。补充命令配置沿用现有环境变量。

代码缺陷进入回修；执行故障按阶段有限重试；证据失效重新检查；修改前基线未建立（如缺少录制响应）不记为阻断，转分析报告交付；只有执行故障次数耗尽或分析报告无法生成时才记录 `blocked`。代码回修次数和各阶段恢复次数分别累计。

### 审批、交付与终态

`prepare_delivery` 创建可审阅的本地候选，`request_approval` 绑定候选身份、提交及当前源码哈希；`publish_delivery` 只交付被批准的同一候选。操作收据和 Git anchor 使重试复用同一提交。
分析报告可按配置直接交付报告分支，随后统一收尾。

正式 `resolution.result` 为 `delivered / analysis_delivered / skipped / blocked / failed / rejected / cancelled`。
等待审批属于运行状态。只接受当前 v7 协议；旧版数据及 checkpoint 不适配、不迁移，需新建执行。
工作流协议 v7 与独立的验证器/AI 审查协议版本分别维护。

## 本地验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m compileall -q app workbench
npm --prefix frontend run build
```

离线测试覆盖正式 Runner 和存储恢复、图路径、验收身份、状态失效、人工审查模式，以及临时 Git 仓库和 bare remote 上的候选/正式推送幂等性。测试不访问业务远程或调用真实模型。

后端专项回归按职责组织：`test_redaction.py` 检查交付脱敏，`test_delivery_reporting.py` 检查交付终态与报告，`test_request_regression.py` 检查请求回归，`test_workspace_boundaries.py` 检查文件过滤与权限，`test_step_inputs.py` 检查输入边界，`test_artifact_policy.py` 检查产物策略。工作流集成测试保留在 `test_workflow.py`，API、日志采集与前端契约测试分别独立维护。新增用例应验证实际生产行为，避免仅比较别名或测试标准库本身。

## Agent 子图

每个 Agent 子图独立维护状态、输入输出协议和 provider；主图只接收各自 `graph.build` 编译后的图。

| Agent | 图节点 | 权限 / 职责 |
|---|---|---|
| `locate` | `locate_agent` | 只读定位证据和候选文件 |
| `repair` | `repair_agent` | 可写，`write_root` 仅限目标 carrier |
| `review` | `review_agent` | 只读审查真实 diff、门禁和回归结果 |

修复输出包括 `passed`、`result`、`analysis`、`summary`、`change_plan`、`blocked_reason` 与 `scope_escalation_required`。审查输出包括 `verdict`、`passed`、`code_verdict`、`coverage`、`summary`、`findings`、`required_changes` 与 `change_plan`，并绑定当前证据版本。

`change_plan` 是两者的正式分析通道：根因确实不在目标 carrier 时给出方案，`scope=project` 的方案需要人工确认；`scope=carrier` 且已就地改完时 `edits` 为空。

Agent 子图的 `State` 使用 TypedDict：**新增字段必须显式声明**，否则会被 LangGraph 静默丢弃（`RepairState.passed`、`ReviewState.change_plan` 都踩过这个坑）。

配置在各自目录的 `config.json`，环境变量 `AI_REPAIR_AGENT_<ROLE>_*` 覆盖。Repair 和 Review provider 仅提供 `claude_session`、`claude_query` 与显式转人工的 `manual` 通道。

所有 Agent 只通过 `app.prompts.PromptRenderer` 组装提示词。Prompt 文件固定为公共 `common.md` 以及每个职责一份角色文件（`locate.md`、`repair.md`、`review.md`）；服务名、carrier、证据、预算和权限范围通过强类型上下文传递，不按项目或 carrier 复制提示词。

### 性能预算与调用审计

每个 Agent 独立配置 `max_turns`、`max_tool_calls`、`max_context_chars`、`max_candidate_files` 与 `timeout_seconds`。定位阶段应优先使用确定性候选文件与符号证据；修复阶段只处理目标 carrier；审查阶段只读取真实 diff、门禁、验证和回归结果。重复的同参数工具调用会被 hook 拦截。审计只保留公开助手文本、工具调用/结果与耗时，不记录隐藏推理链。

## 工作台后端

唯一正式前端位于 `frontend/`，后端使用 FastAPI，执行引擎复用仓库现有 LangGraph 主图、步骤插件、RecordStore 与 checkpoint。已退役并删除的 `workbench/ui/` 不再作为启动或构建入口；旧 `/api` 启动入口复用 v1 的预创建、后台失败报告和生命周期锁。

前端使用 `frontend/src/lib/workbenchQueries.ts` 统一任务、审批、批次和步骤的数据读取，页面只编排组件。
`workbench/view_models.py` 输出操作许可和候选交付身份；GET 中的许可是读取时快照，POST 仍重新检查状态、锁、前置步骤和证据。
任务、步骤、审批审阅使用完整 Handoff 输出；历史工作流只读。本地候选的 `prepared` 状态不表示正式交付，只有 `publish_delivery` 完成后才是 `published`。

正式执行/业务评估、操作许可、交付投影类型和阶段列表从后端生成：

```powershell
.venv/Scripts/python.exe -m app.workflow.frontend_contracts
npm --prefix frontend run build
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -q
```

设计与组件职责见 `frontend/DESIGN.md`。浏览器验收脚本为 `tests/frontend_redesign.cjs`，使用显式 API 样本，覆盖四个工作区和三种屏幕宽度；运行前启动前端，并提供 Playwright 模块（`PLAYWRIGHT_MODULE`）与浏览器通道（`UI_BROWSER_CHANNEL`，默认 msedge）。验收截图写入临时目录，不混入业务运行记录。

工作台能力：

- 运行创建、列表、详情、配置与任务进度。
- 节点时间线及输入、输出、错误和产物引用。
- Provider 请求级模型调用审计：模型、Prompt 哈希、耗时、Token 用量（供应商未返回时为空）、工具调用统计与错误类型。
- 运行状态快照 SSE 和控制操作审计查询。
- 审批队列与 LangGraph checkpoint 恢复。
- 任务板投影（`/runs/{run_id}/board`）与单步骤详情、关联模型调用、上下文检查器。
- 单步骤重跑（`/runs/{run_id}/tasks/{task_id}/steps/{step_name}/run`）：仅在非活跃执行上允许，写步骤必须显式确认。
- 产物读取限制在当前运行/任务目录，限制最大读取大小。

工作台不提供知识案例或人工介入转派 API；人工决策只针对本地候选准备后的 `request_approval` 中断。旧 `apply_plan` 入口已退役，变更方案通过工作流的 `change_plan` 契约流转。

模型调用审计文件写入 `<运行时目录>/runs/<run_id>/model_calls.jsonl`。历史运行不会补造缺失的模型调用记录。Token 等用量字段以 Provider 实际返回为准。

提示词快照写入 `tasks/<task_id>/audit/prompts/<model_call_id>.json`，包含公共规则、角色提示词、实际发送的任务上下文和压缩前完整任务证据。快照由 `workbench_prompt_snapshot` 开关控制（默认开启）；启用快照归档时保留原始字段和值，不脱敏、不裁剪完整归档；是否归档还取决于后文的 AI_REPAIR_ARTIFACT_MODE。模型上下文按预算引用完整证据，页面提供按需展开。历史已脱敏或截断的记录保留原标记。

运行与安全边界：

- `server.py` 默认监听 `0.0.0.0:8000`；未设置 `WORKBENCH_API_TOKEN` 时启动会回落为只监听 `127.0.0.1`（API 无鉴权，仅限本机访问）。对外部署必须设置强随机 `WORKBENCH_API_TOKEN`，并通过 HTTPS 反向代理和网络访问控制限制入口，禁止开放公网匿名访问。
- 运行记录目录应位于持久化磁盘；同时备份 `workflow-checkpoints.sqlite3`、`workbench-control.sqlite3` 与运行产物。
- 工作台不自动合并代码；代码交付仍遵守工作流门禁。
- 当前 SSE 发送的是基于持久化运行记录的状态快照，不等于逐 token 模型流。取消接口目前表达停止请求，当前节点可能完成后才响应。
- 本仓库尚未提供完整的企业 SSO/OIDC 与 RBAC；远程部署前必须补齐统一身份认证、角色授权、反向代理安全策略和端到端验收。
- MANUAL 任务作为终态记录展示，不进入交付审批队列。

## 前端

`frontend/` 包含基于 React、TypeScript、Vite、Ant Design、TanStack Query、项目内拓扑和热力图组件 的工作台 UI。运行态、审批项、步骤、模型调用、审计记录与产物内容均通过受控后端 API 获取。

信息架构：

- 执行阵列（`/runs`）：指标带、run 卡与任务热力条、状态/关键词筛选、分页和发起执行向导。
- Run 工作区（`/runs/:runId`）：run 级拓扑、按优先级分组的任务板、暂停/恢复/停止、只读 checkpoint 时间线与控制审计。
- Task 工作区（`/runs/:runId/tasks/:taskId`）：任务头、真实子图节点轨（组合阶段可展开）、步骤输入/输出/产物/错误、Agent 追踪与上下文检查器。
- Agent 追踪：提示词分段（版本、hash、字符数）、工具调用时间线与模型输出/Token 统计，按 attempt 切换。
- 交付审批（`/approvals`）：左队列右审阅；驳回必须填写理由并二次确认，批准保留分支供人工合并。

设计和状态原则：

1. 顶部为 56px 固定导航（执行阵列、交付审批角标、服务健康），底部固定状态条展示服务、运行目录与待审批数量。
2. 任务是最小观察单元：Run 页只承载任务卡与状态，步骤 I/O 与单步操作全部收敛到 Task 页。
3. 运行状态和业务结论分开展示。等待、失败、拒绝、证据不足、交付等状态不能混为一类。
4. 前端只展示后端授权的数据；产物通过受控 API 获取，不直接读取磁盘、Git 仓库、steps.json 或 LangGraph checkpoint。
5. 人工决策入口收敛到正式交付前的候选审批；`request_approval` 不提供单步执行入口。
6. 单步执行是写操作：必须持运行锁、写入控制审计，写步骤需要显式确认；活跃执行由服务端拒绝并返回 409。
7. 令牌只保存在当前页面内存中。按钮状态不替代服务端的权限、状态机、输入校验、幂等和审计。

API 约定：所有控制面请求通过 `src/lib/api.ts` 发出，路径以 `/api/v1` 为前缀；健康检查使用 `/api/v1/health`（该端点同样受令牌保护，生产环境需带 `Authorization: Bearer <WORKBENCH_API_TOKEN>`；未配置令牌时才会放行）。类型集中于 `src/lib/types.ts`。前端不直接访问数据库、Git 或本地文件系统。

## 本地启动

后端（仓库根目录）：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写日志源、源仓库、模型网关等配置
python -m pip install -r requirements.txt
python server.py
```

前端（另开终端）：

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

访问 Vite 输出的地址，默认 `http://127.0.0.1:5173`。开发代理默认指向 `http://127.0.0.1:8000`，可通过 `WORKBENCH_API_ORIGIN` 覆盖。Vite 会加载 `frontend/.env.local` 中的代理配置，已有进程环境变量优先。API 文档：`http://127.0.0.1:8000/docs`。

生产构建：

```powershell
cd frontend
npm run build
```

`npm run build` 包含 TypeScript 类型检查；只检查类型可运行 `npm run typecheck`。仓库当前没有 CI 工作流，部署前仍需验证审批状态切换、刷新与断线恢复、产物授权和运行控制。

## 真实数据单步调试

单步命令必须使用整图执行产生的真实 `run_id` 和任务 `task_id`。步骤从 `workflow/state.json` 与正式 artifact 恢复完整输入，`steps.json` 仅用于审计展示，不接收 fixture、stub 或手写问题数据；缺少通过的前置记录时直接拒绝。

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

`runtime/workspaces` 是可重建缓存：`shared/cache-<key>` 按 Git commit、业务子目录和过滤规则指纹复用只读源码基线，`shared/refs/<run>.json` 固定每轮使用的版本；同一提交的多轮执行共用一棵树。`ai/<run>-<task>` 只保存任务可修改的运输商代码，`delivery/<branch>` 与 `regression/<task>` 是临时 Git 克隆。不读取旧 `shared/<run>` 目录，也不迁移旧 checkpoint。

工作区在任务收尾、执行退出、服务启动及运行期间定时回收。`AI_REPAIR_WORKSPACE_GC_INTERVAL_SECONDS` 默认 3600 秒；`AI_REPAIR_WORKSPACE_RETENTION_DAYS` 默认 7 天，从生命周期结束时间计算，0 表示终态后可立即回收。暂停、待审批、验证受阻（BLOCKED）和活动任务的基线引用保留；只有所有引用均过期后才删除共享基线。验证受阻任务保留 AI 工作区供后续排查；其他已结束任务的 AI 工作区按任务自身保留期清理。临时交付／回归克隆按 `AI_REPAIR_DELIVERY_WORKSPACE_TTL_HOURS`（默认 24 小时）清理，存在活动 worker 时保守保留。`AI_REPAIR_WORKSPACE_GC_ENABLED=false` 关闭自动回收。审计记录、源码快照和恢复指针保存在 `runtime/runs`，不参与工作区回收；过期工作区被清理后，重新修改源码应发起新任务。

每轮使用一个 `records.sqlite` 集中保存 run/task 生命周期、模型调用元数据、Agent 指标和步骤／产物历史；`steps.json`、报告、Diff、验证证据及恢复数据保持原有独立文件。生命周期、模型调用、指标和历史仅使用集中存储，不读取旧 JSON/JSONL 文件。

`AI_REPAIR_ARTIFACT_MODE=compact`（默认）保留完整校验后 Agent 结论、调用摘要和统计，不归档完整提示词、公开事件及重复的原始模型输出。设为 `debug` 后额外保存这些调试产物（提示词快照仍受 `workbench_prompt_snapshot` 控制）。工作台在精简模式下仍可查看调用统计与结论，提示词快照和事件显示为未保存。模式在创建运行时记入 `run.json`，后续恢复沿用该轮模式，配置变更只影响新运行。

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

> 供应链现状：`yq_common` 仍取自内网 Nexus 的**明文 HTTP** 直接 URL
> （`http://n.17track.net:8081/.../yq_common-0.0.77-py3-none-any.whl`），因为该仓库
> 只支持 HTTP（HTTPS 握手实测失败）；Dockerfile 保留 `--trusted-host` 以允许该 HTTP 源。
> 该 wheel 已改用 PEP 508 直接引用并带 `#sha256=...` 内容哈希锁定，传输内容被替换会
> 在安装时失败。TLS 依赖 Nexus 侧升级，升级前不要删除 `--trusted-host`。
> 也不再使用 `constraints.txt`；如果要进一步启用 `--require-hashes`，需要先为全部
> 传递依赖补齐哈希，当前仓库无法满足。

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f api frontend
```

前端默认监听 `http://localhost:8080`。API 不直接映射宿主机端口；远程部署必须在 HTTPS 反向代理后访问。容器需要 Git 远程访问权限，必须确认 Git 身份、分支策略和凭据可用。默认不自动推送，禁止自动合并。不要将 `.env`、密钥或运行数据打入镜像。

## 准生产目录

根目录保留运行源码 `app/`、`config/`、`workbench/`，前端源码及构建配置 `frontend/`，服务入口 `server.py`、运行依赖清单 `requirements.txt`、环境配置、Docker 部署文件和本说明。`.git/` 保留版本记录；本机 `.venv/`、前端 `node_modules/` 和 `dist/` 用于当前运行及构建，不进入容器构建上下文。

`tests/`（单元测试与 Playwright 验收脚本）、`logs/`、`runtime/` 仍留在仓库里，但都不进镜像构建上下文（见 `.dockerignore`），发布时应按发布清单排除。业务回归执行器、Agent 提示词和真实运行记录属于生产功能，不按测试文件清理。运行数据由 `AI_REPAIR_RUNTIME_DIR` 管理，不作为发布源码。

准生产检查使用后端 `/api/v1/health` 和前端构建；健康接口应报告 `review_contract_version=6、workflow_version=7`、`log_redaction_enabled=false`。修改后需重启后端才能加载新代码。

## 日志查询范围

工作台按“最近 N 小时”的滚动时间窗口查询日志，默认值由 `config/settings.py` 的
`default_log_hours` 统一定义，`LOG_PULL_HOURS` 或新建执行的 `log_hours` 可覆盖。
新建执行弹窗从 `/api/v1/run-config` 获取有效默认值与上限，API、CLI 和前端统一遵循
“显式运行参数 → 环境配置（进程变量优先于 `.env`）→ settings 默认值”。
审查模式、最大尝试次数、日志条数上限也遵循同一规则；重试和单步恢复优先保留原运行参数，包含 `log_limit=0`。
按日 ES 索引覆盖范围由查询小时数自动计算（`ceil(hours / 24) + 1`），索引日期使用日志时区；不再单独配置索引天数或索引时区。
新建执行默认 `log_limit=0`，表示窗口内全量拉取，没有 1,000 / 10,000 条的隐藏截断，
也没有扫描总时长截断；单次 ES 请求仍保留网络超时。
前端不再显示日志条数限制输入，读取阶段会展示已扫描、可解析和独立问题计数。

审查内部检查不再支持独立重跑；单步执行和审批驳回后的验证恢复统一进入 `review_agent`。内部检查的完整记录由审查节点产物引用访问。工作流拓扑升级至 v7，旧版运行不再提供兼容读取，需新建批次使用当前流程。
