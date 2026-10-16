# 模型字段与配置字段说明

本文统一说明 Pydantic 输入/输出模型、工作流状态、Agent 配置与部署变量。Python 模型字段优先使用 `Field(description=...)`；未使用 Field 描述的字段在定义处增加中文注释。修改模型后应同步更新本文和示例配置。

## 模型契约总览

| 模型/契约 | 字段责任 |
|---|---|
| `RunState` | Run 级发现、筛选参数、任务清单、串行游标和任务结果累加 |
| `WorkflowState` | 单个任务的源码快照、证据、定位、修复、门禁、验证、回归、审查、审批、交付与终态 |
| `StepRecord` | 节点身份、执行状态、业务结论、尝试次数、时间、耗时、摘要、错误、产物引用、输入输出和 schema 版本 |
| `LocateInput` | Run/Task 身份、原始问题身份、证据、源码快照位置；校验身份字段与原始问题一致 |
| `LocateOutput` | 定位状态、根因、事实、源码证据、候选文件、证据缺口、建议范围与摘要 |
| `RepairInput` | 任务 ID、隔离工作区、尝试序号、回修原因、确定性证据和定位结论 |
| `RepairView` | 是否通过、修复结论、分析摘要、模型声明的变更文件、结构化变更方案和范围升级原因 |
| `ReviewInput` | 修复结论、真实 diff 摘要、门禁/验证/回归结果和变更范围 |
| `ReviewView` | PASS/FAIL/MANUAL、审查发现、必须修改项及可选结构化方案 |
| `PromptContext` | Agent 角色、Run/Task 身份、异常原文、源码快照、证据、变更范围、停止条件和调用预算 |
| `CheckInput / CheckOutput` | 目标范围、基线、授权文件、逐文件 diff、安全命中、语法结果和门禁结论 |
| `ApprovalInput / ApprovalOutput` | 任务、审批指纹、变更文件、授权范围、审批决定与最终门禁结果 |
| `RegressionInput / RegressionOutput` | 目标运输商、隔离工作区、改动文件、回归结论、原因和执行命令数量 |
| `DeliveryInput / DeliveryOutput` | 修复分支、变更文件、审查/审批门禁、提交标识与推送结果 |
| `FinalizeInput / FinalizeOutput` | 任务身份、终态、最终报告路径和最终业务结论 |
| `RunConfig / RuntimeConfig / Settings` | 日志来源、工作区路径、模型连接、Git 行为、验证命令与运行限制 |
| `LocateConfig / RepairConfig / ReviewConfig` | 各 Agent 独立的 Provider、工具白名单、回合/调用/上下文/候选/差异/超时预算 |

## 关键字段语义

- `status`：执行状态或业务状态，具体语义以所属模型为准；不可与 `result` 混用。
- `result` / `verdict` / `decision`：业务结论、审查结论或人工决定。审查结论使用 PASS、FAIL、MANUAL；审批决定使用 PENDING、APPROVE、REJECT。
- `attempt`：当前修复尝试序号，从 1 开始，只由受控重试节点递增。
- `changed_files`：模型声明的文件列表不是可信事实；必须与基线对比得到的真实 diff 对账。
- `baseline_manifest` / `baseline_dir`：修复前文件哈希和只读基线副本，用于检测越界改动与验证后文件变化。
- `authorized_files`：仅在明确审批授权后扩展允许范围，不可由模型自行写入。
- `change_plan`：共享层或范围升级方案；存在方案不等于已授权或已应用。
- `approval_fingerprint`：绑定本次审批内容的指纹，避免用旧审批授权新差异。
- `events`：公开的助手文本、工具调用和工具结果；不存储隐藏推理链或凭据。

## Agent JSON 配置字段

JSON 不支持注释，逐字段说明集中在本表。

| 字段 | 含义 | 约束/用途 |
|---|---|---|
| `description` | Agent 用途说明 | 仅用于运维展示，不作为权限边界 |
| `enabled` | 是否启用 Agent | 关闭时调用方必须明确处理 |
| `provider` | 调用适配器 | locate 当前为 `claude_query`；repair/review 使用 `claude_session` |
| `writable` | 是否允许写文件 | locate 必须 false；review 没有写权限开关；repair 仍受路径 hook 限制 |
| `tools` | 工具白名单 | locate/review 仅 Read、Glob、Grep；repair 为 Read、Glob、Grep、Write、Edit |
| `max_turns` | 模型交互回合预算 | 防止无限对话循环 |
| `max_tool_calls` | 工具调用次数预算 | 防止过度搜索和重复调用 |
| `max_context_chars` | 原始异常/任务上下文字符预算 | locate 默认 50,000 字符；超出时保留原始异常并转人工，不静默截断；渲染后的 Prompt 另预留 30,000 字符结构开销 |
| `max_candidate_files` | 候选源码文件数量上限 | 限制检索范围 |
| `max_diff_chars` | 审查差异证据字符上限 | 避免重复注入大体积 diff |
| `timeout_seconds` | Agent 超时秒数 | 到时必须终止调用并记录错误 |
| `options` | Provider 扩展选项 | 只能传入适配器认可的选项 |

当前配置文件：
- locate：14 回合、12 次工具调用、50,000 字符原始问题预算（Prompt 结构开销另加 30,000 字符）、6 个候选文件、480 秒超时。
- repair：16 回合、20 次工具调用、18,000 字符上下文、6 个候选文件、12,000 字符差异预算、480 秒超时。
- review：20 回合、24 次工具调用、24,000 字符上下文、8 个候选文件、16,000 字符差异预算、900 秒超时。

## 应用配置字段

| 环境变量 | 含义 |
|---|---|
| `LOG_ES` | Elasticsearch 地址 |
| `LOG_PULL_SERVICE_NAME` | 必须精确匹配的服务名称 |
| `LOG_PULL_SERVICE_PREFIX` | 服务名前缀筛选 |
| `LOG_PULL_DAYS` / `LOG_PULL_LIMIT` | 日志回溯天数与条数上限 |
| `LOG_PULL_TZ_OFFSET` | 日志时区偏移小时数 |
| `LOG_PULL_IGNORE_ERRORS` | 忽略的错误消息清单 |
| `LOG_PULL_EXCLUDED_BIZTYPES` | 排除的业务类型清单 |
| `AI_REPAIR_SOURCE_REPO` | 容器/主机内可访问的源 Git 仓库路径 |
| `AI_REPAIR_PROJECT_SUBDIR` | 仓库内项目子目录 |
| `AI_REPAIR_RUNTIME_DIR` | 运行记录、SQLite checkpoint、资格与审计的持久化目录 |
| `AI_REPAIR_VERIFY_COMMANDS` | JSON 数组格式的验证命令 |
| `AI_REPAIR_GIT_REMOTE` / `AI_REPAIR_GIT_PUSH` | Git 远程名与是否允许自动推送 |
| `AI_REPAIR_REVIEW_MODE` | `agent` 或 `human` |
| `AI_REPAIR_MAX_ATTEMPTS` | 单任务最大修复尝试次数，1–5 |
| `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` | 模型认证、网关地址和默认模型 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` / `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` | 不同复杂度的模型标识 |
| `WORKBENCH_API_TOKEN` | 工作台 Bearer Token；容器部署必须配置 |
| `AI_REPAIR_SOURCE_REPO_HOST_PATH` | Docker Compose 使用的宿主机 Git 仓库路径，只用于挂载 |
| `WORKBENCH_PORT` | 前端对外监听端口，默认 8080 |
| `APP_UID` / `APP_GID` | API 容器运行 UID/GID，用于匹配宿主机仓库权限 |

## 配置变更规则

1. 新增字段必须有类型、默认值/必填策略、中文说明、边界校验和测试。
2. 不允许通过配置放宽 locate/review 的只读工具边界。
3. 配置来源优先级必须明确记录；环境变量覆盖配置文件，密钥不进入 JSON 配置文件。
4. 改动调用预算时同时更新本表、Agent 配置 JSON 和耗时/重试测试。
