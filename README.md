# AI 异常日志自动修复工作流

项目按 `domain / application / workflow / agents / infrastructure / prompts` 分层。当前工作流版本为 **v5**，由确定性步骤插件、三个 Agent 和 LangGraph 编排组成。前端位于 `frontend/`，后端 API 位于 `workbench/api.py`，服务入口为根目录 `server.py`。

仓库文档统一收敛在本文件；`app/`、`app/agents/`、`app/workflow/steps/`、`frontend/`、`workbench/` 不再单独维护 README。

## 目录

- [主流程](#主流程)
- [代码结构](#代码结构)
- [工作步骤](#工作步骤)
- [验证与统一决策](#验证与统一决策)
- [Agent 子图](#agent-子图)
- [工作台后端](#工作台后端)
- [前端](#前端)
- [本地启动](#本地启动)
- [真实数据单步调试](#真实数据单步调试)
- [Docker 部署](#docker-部署)
- [准生产目录](#准生产目录)
- [日志查询范围](#日志查询范围)

## 主流程

```text
run 级（一次执行跑一遍）:
sync_source_repo（同步源仓库）
  -> discover_and_normalize（discover_logs -> normalize_error -> deduplicate_error）
  -> identify_carrier
  -> task_dispatch（串行游标，每次只放行一个任务）
  -> 任务级子图（每个任务按 tasks 顺序串行执行）:
     prepare_context
       （prepare_source_snapshot -> repair_branch_preflight -> prepare_workspace -> prepare_evidence）
     -> locate_agent
     -> claim_repair_slot
     -> repair_agent
     -> validate_changes（check_changes -> syntax_regression）
     -> plan_validation -> verify_issue -> run_regression
     -> summarize_validation -> review_agent -> decide_validation
        ├─ retry：经 retry_prepare 恢复对应验证或审查阶段
        ├─ repair：经 retry_prepare 返回 repair_agent
        ├─ blocked：finalize_execution，记录验证受阻
        └─ deliver：prepare_delivery -> request_approval -> finalize_execution
  -> task_collect（记下结论，推进游标）-> 回到 task_dispatch
```

任务之间严格串行：前一个任务走到 `finalize_execution` 并记完汇总后，游标才推进到下一个。任务子图持有自己的状态（`WorkflowState`），父图只保留 run 级字段与结果汇总（`RunState`），所以 `changed_files`、`repair`、`locate` 这类任务局部通道不会跨任务累积。

框架预检优先识别 `gathers/<Carrier>/main.py`，缺少新框架入口但存在 `spiders/<Carrier>/` 时保留旧框架证据标签；定位 Agent 继续只读分析，后续按根因范围和定位结论路由。旧框架或依赖旧实现的标签本身不会在预检阶段直接终止任务。缺少两类实现时步骤明确失败。

上图是正常修复路径，定位结论、取消和交付失败等分支由 `definitions.py` 与 `routes.py` 决定。`review_mode=human` 只跳过 AI 审查：`summarize_validation → decide_validation`，仍要求完成两项业务验证后才能交付。

## 代码结构

```text
app/
├── domain/                  models/, errors.py, policies.py, decisions.py
├── application/             context.py, contracts.py, services.py, audit.py
├── workflow/
│   ├── graph.py             LangGraph run 级主图 + 任务子图
│   ├── definitions.py       业务阶段组合与任务子图转移定义
│   ├── routes.py            门禁路由
│   ├── state.py             RunState（父图）与 WorkflowState（任务子图）
│   ├── registry.py          步骤与 Agent 注册
│   ├── runner.py            单步骤记录执行器
│   ├── validation.py        v5 验证计划、事实汇总与统一决策契约
│   └── steps/<step>/        19 个确定性步骤插件；__init__.py 导出 SPEC
├── agents/                  common.py, policy.py, locate/, repair/, review/
├── infrastructure/          es/, git/, filesystem/, claude/, commands/, records/
├── prompts/                 templates/{common,locate,repair,review}.md, loader.py, renderer.py
└── entrypoint.py            整图入口
```

步骤包按复杂度拆分契约与执行器；小型平台步骤可以直接在 `__init__.py` 内定义，不要求每个目录都有 `models.py`、`node.py` 或 `__main__.py`。`plugin_loader.py` 显式注册步骤和 Agent，`definitions.py` 定义阶段组合与转移，阶段内部调用插件并分别写入记录。

提供 `__main__.py` 的步骤可以通过模块 CLI 调试；所有单步执行均使用真实运行记录重建输入。`__init__.py` 是 Python 包入口，`__pycache__/` 是运行时字节码缓存，都不是额外步骤。

## 工作步骤

目录名、`SPEC.name` 与步骤记录名保持一致。组合阶段内部按顺序调用插件；阶段之间按路由推进。原始问题验证、真实请求回归即使未通过，也要把实际结果送入汇总与统一决策，不能在中间直接认定修复成功或转人工。

### 工作流主节点

| 主节点 | 内部执行 / 职责 | 通过后的下一阶段 |
|---|---|---|
| `sync_source_repo` | 同步远程源仓库，保存同步记录与 checkpoint | `discover_and_normalize` |
| `discover_and_normalize` | `discover_logs → normalize_error → deduplicate_error` | `identify_carrier` |
| `identify_carrier` | 按日志原始身份字段创建去重任务清单 | 串行任务分发 |
| `prepare_context` | `prepare_source_snapshot → repair_branch_preflight → prepare_workspace → prepare_evidence` | `locate_agent` |
| `locate_agent` | 只读定位；仅证据支持的 `LOCATED` 可继续 | `claim_repair_slot` |
| `claim_repair_slot` | 原子领取本轮修复资格 | `repair_agent` |
| `repair_agent` | 仅修改授权范围内的目标 carrier | `validate_changes` |
| `validate_changes` | 范围与语法检查 | `plan_validation` |
| `plan_validation` | 绑定补丁、基线、原始输入和检查策略，确定验收条件 | `verify_issue` |
| `verify_issue` | 用原始录制正文比较修复前后行为 | `run_regression` |
| `run_regression` | 真实请求经 server_dev.main 执行，保留原参数和代理 | `summarize_validation` |
| `summarize_validation` | 汇总验证事实，检查证据是否过期 | `review_agent`；human 模式到 `decide_validation` |
| `review_agent` | 只读审查实际代码与统一验证事实 | `decide_validation` |
| `decide_validation` | 决定补证重试、代码回修、交付或验证受阻 | 对应恢复阶段、交付或收尾 |
| `prepare_delivery` | 创建修复分支并按配置提交/推送；明确转分析报告的任务交付报告分支 | 源码交付后进入 `request_approval`；分析报告交付后结束 |
| `request_approval` | 已交付源码分支的人工决策；拒绝进入有上限的回修循环 | `finalize_execution` 或回修 |
| `finalize_execution` | 生成终态报告并收尾 | 结束 |

`TASK_NODE_NAMES` 定义 15 个任务编排节点（含内部控制节点 `retry_prepare`）。这与插件目录数量不同：组合阶段会执行多个插件，三个 Agent 有各自的子图，部分步骤只在 run 级执行。run 级还包含源码同步、任务分发和汇总等编排节点。

### 原子步骤

`app/workflow/steps/` 共 **19 个正式步骤目录**：原有 16 个，加上 v5 的 `plan_validation`、`summarize_validation`、`decide_validation`。之前的“五个改造阶段”是实施顺序，不是运行时节点数。以下清单与 `plugin_loader.BUILTIN_STEPS` 对应。

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
| `syntax_regression` | task | 独立编译变更的 Python 文件，使用临时 pycache，不代替业务验证 |
| `plan_validation` | task | 确定验收条件并绑定当前证据版本 |
| `verify_issue` | task | 比较原始异常在基线与补丁代码中的行为 |
| `run_regression` | task | 经 `server_dev.main` 发起真实请求并逐项核对业务结果 |
| `summarize_validation` | task | 汇总独立验证事实与证据有效性 |
| `decide_validation` | task | 根据事实、审查结论和次数上限决定下一步 |
| `request_approval` | task | 保存人工决策 |
| `prepare_delivery` | task | 生成修复分支与交付结果 |
| `finalize_execution` | task | 汇总该任务的步骤记录、状态和产物 |

Locate、Repair、Review Agent 仍分别是独立 Agent 子图，不与确定性步骤合并。

### 终态语义

`status` 记录执行状态，原异常效果、真实请求结果、代码审查分别记录。`decide_validation` 统一决定交付、回修、有界恢复或 BLOCKED。BLOCKED 表示验证受阻，保留补丁和有效证据，不自动进入审批。

human 模式（人工审查）与 agent 模式的差异只在审查环节：回归通过后同样交付修复分支并进入交付审批，由人工审查分支内容。终态按真实交付记录推导——没有真实提交（远端分支或源仓库本地分支可查）时不会记为 `SUCCESS`。

## 验证与统一决策

### 计划、事实与动作

| 节点 | 保存什么 | 用户需要核对什么 |
|---|---|---|
| `plan_validation` | `ValidationPlan`：补丁/基线哈希、输入指纹、检查策略版本、变更符号和验收条件 | 本次验证针对哪个问题、哪个补丁，需要证明什么 |
| `summarize_validation` | `ValidationSummary`：原异常与真实请求分别为 `passed / failed / unverified`，附原因和报告路径 | 原问题是否复现并修复、实际请求是否完成、是否仍有证据缺口 |
| `decide_validation` | `ValidationDecision`：下一动作、目标阶段、原因、恢复次数和代码回修次数 | 为什么继续、回修、交付或停止 |

这三个节点不调用模型。修复 Agent 的 `validation_notes` 是建议，不直接成为硬性验收门槛；不执行模型构造的响应样本来证明业务修复。

### 原始问题验证与真实请求回归

`verify_issue` 在隔离的修复前、修复后代码副本中重放**同一份原始录制正文**。通过必须证明基线复现原异常，且补丁代码满足修复后的协议要求。没有原始正文或异常证据时记录未验证，不能拿后来请求得到的新正文替代原始问题证据。

`run_regression` 在独立 Git 回归克隆内运行真实业务入口，不注入录制响应。生成脚本保留日志中的原始单号和请求字段，从异常上下文提取 `track_config`，包括 `proxy_channel_model`；不会把单号替换为 `***`。调用形式为：

```python
from server_dev import main
from framework.models import (
    TrackModel, TrackRequestItemModel, TrackRequestConfigModel, ProxyChannelModel,
)

reqs = [TrackRequestItemModel(number="CPBPQKZ0518229001")]
res = main(TrackModel(
    carrier="CouriersPlease",
    requests=reqs,
    track_config=TrackRequestConfigModel(
        timeout=12000000,
        proxy_channel_model=ProxyChannelModel(**{
            "biz_ident": "packet_CouriersPlease",
            "provider": "proxy_scrape",
            "proxy_type": "datacenter",
        }),
    ),
))
```

示例只说明调用形式，实际参数来自该任务日志；已有 `timeout` 保留，缺省时补 `12000000`。业务超时参数与执行器的进程超时是两层限制。

当前代理地址读取工作流服务进程环境变量 `PROXY_MANAGER_ADDRESS`。启动后端前设置：

```powershell
$env:PROXY_MANAGER_ADDRESS = "192.168.25.201:31656"
python server.py
```

回归环境准备阶段把该值写入隔离克隆的 `.env`：`proxy_manager_address="192.168.25.201:31656"`；真实请求还校验实际代理配置已生效。不要只配置开发机业务仓库的 `.env`，却遗漏运行工作流服务的进程环境。业务仓库的运输商脚本不为此改写。

`server_dev.main` 可能只打印 `result:` 而返回 `None`，因此以实际业务结果和逐单匹配为准，不以 `res is None` 或进程退出码 0 判断成功。完整执行脚本、请求参数、stdout/stderr、业务结果和证据报告分别归档。

| 结果 | 如何处理 |
|---|---|
| 有效 `Succeed` 或 `NotFound`，请求项完整匹配 | 请求验证通过；无轨迹不等于请求失败 |
| 实际 `ProcessError` 或已捕获的业务处理错误 | 业务验证失败，进入代码回修判断 |
| `WebError / ServiceError / TimeoutError` | 未验证；保留实际分类，不据此断言原异常修复无效或一定是代理故障 |
| 进程超时、环境启动失败、结果缺失或无法解析 | 未验证，不伪造业务返回类型 |
| 仅语法通过、未配置执行器或未执行真实请求 | 不能算业务验证通过 |

### 恢复、回修与审批

| 情况 | 统一决策 |
|---|---|
| 补丁、输入或检查策略变化，已有证据失效 | 重新检查并建立验证计划 |
| 可恢复的执行故障或审查协议错误 | 只恢复对应验证/审查阶段 |
| 业务验证确认失败或代码审查确认缺陷 | 返回修复 Agent |
| 两项业务验证通过，AI 代码审查通过 | 进入交付及最终审批 |
| 两项业务验证通过，用户明确选择 human 模式 | 交付分支，交由人工代码审查 |
| 必需证据不可得、存在未解决判断或次数耗尽 | `BLOCKED`，显示“验证受阻”，保留补丁和有效证据 |

恢复次数按目标阶段分别记录，与代码回修次数分开。`max_attempts` 默认 3，即每个恢复阶段最多追加 2 次自动恢复；代码修复最多 3 次尝试。已通过且输入、补丁一致的真实请求结果可复用，解析补证不必重复发请求。

AI 审查协议为 v5：`verdict` 仅允许 `PASS / FAIL / MANUAL`，不接受旧 `result` 别名；另行记录 `code_verdict` 与 `coverage.reviewed_symbols`。AI 输出 PASS 只代表代码审查结论，交付资格由 `decide_validation` 产生。证据不足或 MANUAL 不会自动进入交付审批。

v5 不恢复旧版执行：旧任务的续跑、单步重跑和审批被拒绝，不迁移检查点；历史记录保留可读，需要执行时新建当前版本任务。更新代码后需重启后端加载新逻辑，工作流不自动合并分支。

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

工作台能力：

- 运行创建、列表、详情、配置与任务进度。
- 节点时间线及输入、输出、错误和产物引用。
- Provider 请求级模型调用审计：模型、Prompt 哈希、耗时、Token 用量（供应商未返回时为空）、工具调用统计与错误类型。
- 运行状态快照 SSE 和控制操作审计查询。
- 审批队列与 LangGraph checkpoint 恢复。
- 任务板投影（`/runs/{run_id}/board`）与单步骤详情、关联模型调用、上下文检查器。
- 单步骤重跑（`/runs/{run_id}/tasks/{task_id}/steps/{step_name}/run`）：仅在非活跃执行上允许，写步骤必须显式确认。
- 产物读取限制在当前运行/任务目录，限制最大读取大小。

工作台不提供知识案例或人工介入转派 API；人工决策只针对交付后的 `request_approval` 中断。旧 `apply_plan` 入口已退役，变更方案通过工作流的 `change_plan` 契约流转。

模型调用审计文件写入 `<运行时目录>/runs/<run_id>/model_calls.jsonl`。历史运行不会补造缺失的模型调用记录。Token 等用量字段以 Provider 实际返回为准。

提示词快照写入 `tasks/<task_id>/audit/prompts/<model_call_id>.json`，包含公共规则、角色提示词、实际发送的任务上下文和压缩前完整任务证据。快照由 `workbench_prompt_snapshot` 开关控制（默认开启）；启用快照归档时保留原始字段和值，不脱敏、不裁剪完整归档；是否归档还取决于后文的 AI_REPAIR_ARTIFACT_MODE。模型上下文按预算引用完整证据，页面提供按需展开。历史已脱敏或截断的记录保留原标记。

运行与安全边界：

- `server.py` 监听 `0.0.0.0:8000`。远程部署必须设置 `WORKBENCH_API_TOKEN`，并通过 HTTPS 反向代理和网络访问控制限制入口，禁止开放公网匿名访问。
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
5. 人工决策入口收敛到交付后的最终审批；`request_approval` 不提供单步执行入口。
6. 单步执行是写操作：必须持运行锁、写入控制审计，写步骤需要显式确认；活跃执行由服务端拒绝并返回 409。
7. 令牌只保存在当前页面内存中。按钮状态不替代服务端的权限、状态机、输入校验、幂等和审计。

API 约定：所有控制面请求通过 `src/lib/api.ts` 发出，路径以 `/api/v1` 为前缀；健康检查使用 `/api/health`。类型集中于 `src/lib/types.ts`。前端不直接访问数据库、Git 或本地文件系统。

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

`runtime/workspaces` 是可重建缓存：`shared/cache-<key>` 按 Git commit、业务子目录和过滤规则指纹复用只读源码基线，`shared/refs/<run>.json` 固定每轮使用的版本；同一提交的多轮执行共用一棵树。`ai/<run>-<task>` 只保存任务可修改的运输商代码，`delivery/<branch>` 与 `regression/<task>` 是临时 Git 克隆。旧 `shared/<run>` 目录继续兼容，不自动迁移历史 checkpoint。

工作区在任务收尾、执行退出、服务启动及运行期间定时回收。`AI_REPAIR_WORKSPACE_GC_INTERVAL_SECONDS` 默认 3600 秒；`AI_REPAIR_WORKSPACE_RETENTION_DAYS` 默认 7 天，从生命周期结束时间计算，0 表示终态后可立即回收。暂停、待审批、验证受阻（BLOCKED）和活动任务的基线引用保留；只有所有引用均过期后才删除共享基线。验证受阻任务保留 AI 工作区供后续排查；其他已结束任务的 AI 工作区按任务自身保留期清理。临时交付／回归克隆按 `AI_REPAIR_DELIVERY_WORKSPACE_TTL_HOURS`（默认 24 小时）清理，存在活动 worker 时保守保留。`AI_REPAIR_WORKSPACE_GC_ENABLED=false` 关闭自动回收。审计记录、源码快照和恢复指针保存在 `runtime/runs`，不参与工作区回收；过期工作区被清理后，重新修改源码应发起新任务。

每轮使用一个 `records.sqlite` 集中保存 run/task 生命周期、模型调用元数据、Agent 指标和步骤／产物历史；`steps.json`、报告、Diff、验证证据及恢复数据保持原有独立文件。读取兼容旧 `lifecycle.json`、`model_calls.jsonl`、指标 JSONL 和历史目录，历史数据不批量迁移。

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

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f api frontend
```

前端默认监听 `http://localhost:8080`。API 不直接映射宿主机端口；远程部署必须在 HTTPS 反向代理后访问。容器需要 Git 远程访问权限，必须确认 Git 身份、分支策略和凭据可用。默认不自动推送，禁止自动合并。不要将 `.env`、密钥或运行数据打入镜像。

## 准生产目录

根目录只保留运行源码 `app/`、`config/`、`workbench/`，前端源码及构建配置 `frontend/`，服务入口、运行依赖清单、环境配置、Docker 部署文件和本说明。`.git/` 保留版本记录；本机 `.venv/`、前端 `node_modules/` 和 `dist/` 用于当前运行及构建，不进入容器构建上下文。

测试脚本、诊断产物、历史审计文档、开发工具配置及缓存已移出项目。业务回归执行器、Agent 提示词和真实运行记录属于生产功能，不按测试文件清理。运行数据由 `AI_REPAIR_RUNTIME_DIR` 管理，不作为发布源码。

准生产检查使用后端 `/api/health` 和前端构建；健康接口应报告 `review_contract_version=5`、`log_redaction_enabled=false`。修改后需重启后端才能加载新代码。

## 日志查询范围

工作台按“最近 N 小时”的滚动时间窗口查询日志，默认值由 `config/settings.py` 的
`default_log_hours` 统一定义，`LOG_PULL_HOURS` 或新建执行的 `log_hours` 可覆盖。
新建执行弹窗从 `/api/v1/run-config` 获取有效默认值与上限，API、CLI 和前端统一遵循
“显式运行参数 → 环境配置（进程变量优先于 `.env`）→ settings 默认值”。
审查模式、最大尝试次数、日志条数上限也遵循同一规则；重试和单步恢复优先保留原运行参数，包含 `log_limit=0`。
按日 ES 索引覆盖范围由查询小时数自动计算（`ceil(hours / 24) + 1`），索引日期使用日志时区；不再单独配置索引天数或索引时区。
旧部署的 `LOG_PULL_DAYS` 仅作为未设置小时数时的兼容回退，新配置统一使用小时数。
新建执行默认 `log_limit=0`，表示窗口内全量拉取，没有 1,000 / 10,000 条的隐藏截断，
也没有扫描总时长截断；单次 ES 请求仍保留网络超时。
前端不再显示日志条数限制输入，读取阶段会展示已扫描、可解析和独立问题计数。
