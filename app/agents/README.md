# Agent 子图

每个 Agent 子图独立维护状态、输入输出协议和 provider；主图只接收各自 `graph.build` 编译后的图。

| agent | 图节点 | 子图 | 读写 |
|---|---|---|---|
| `locate` | `locate_agent` | 只读定位证据和候选文件 |
| `repair` | `repair_agent` | 可写，`write_root` 仅限目标 carrier |
| `review` | `review_agent` | 只读审查真实 diff、门禁和回归结果 |

修复对外只留 `passed` / `result` / `analysis` / `summary` / `change_plan` / `blocked_reason` / `scope_escalation_required`。
审查对外只留 `verdict` / `passed` / `summary` / `findings` / `required_changes` / `change_plan`。

`change_plan` 是两者的正式分析通道：根因确实不在目标 carrier 时给出方案，`scope=project`
的方案需要人工确认；`scope=carrier` 且已就地改完时 `edits` 为空。

Agent 子图的 `State` 使用 TypedDict：**新增字段必须显式声明**，否则会被 LangGraph 静默丢弃
（`RepairState.passed`、`ReviewState.change_plan` 都踩过这个坑）。

配置在各自目录的 ``config.json``，环境变量 ``AI_REPAIR_AGENT_<ROLE>_*`` 覆盖。Repair 和 Review
provider 仅提供 `claude_session`、`claude_query` 与显式转人工的 `manual` 通道。

所有 Agent 只通过 `app.prompts.PromptRenderer` 组装提示词。Prompt 文件固定为公共 `common.md`
以及每个职责一份角色文件（`locate.md`、`repair.md`、`review.md`）；服务名、carrier、证据、预算
和权限范围通过强类型上下文传递，不按项目或 carrier 复制提示词。

## 性能预算与调用审计

每个 Agent 独立配置 `max_turns`、`max_tool_calls`、`max_context_chars`、`max_candidate_files` 与 `timeout_seconds`。定位阶段应优先使用确定性候选文件与符号证据；修复阶段只处理目标 carrier；审查阶段只读取真实 diff、门禁、验证和回归结果。重复的同参数工具调用会被 hook 拦截。审计只保留公开助手文本、工具调用/结果与耗时，不记录隐藏推理链。字段定义见 `docs/MODEL_AND_CONFIG_REFERENCE.md`。
