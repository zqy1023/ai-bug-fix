# 工作步骤

目录名、`SPEC.name`、步骤记录名与调试模块名必须一致。原子步骤继续独立注册、独立记录、独立调试；LangGraph 图只调度业务阶段，组合阶段内部按顺序调用原子步骤，失败或人工介入后立即停止。

## 工作流主节点

| 主节点 | 内部执行 / 职责 | 通过后的下一阶段 |
|---|---|---|
| `discover_and_normalize` | `discover_logs → normalize_error → deduplicate_error` | `identify_carrier` |
| `identify_carrier` | 按日志原始身份字段创建去重任务清单 | 串行任务分发 |
| `prepare_context` | `prepare_source_snapshot → prepare_workspace → prepare_evidence` | `locate_agent` |
| `locate_agent` | 只读定位；仅证据支持的 `LOCATED` 可继续 | `claim_repair_slot` |
| `claim_repair_slot` | 原子领取本轮修复资格 | `repair_agent` |
| `repair_agent` | 仅修改授权范围内的目标 carrier | `validate_changes` |
| `validate_changes` | `check_changes → verify_issue`，任一步失败即停止 | `run_regression` |
| `run_regression` | 在本地准生产 Git 环境执行回归，不提交 | `review_agent`（agent 模式）或直接收尾（human 模式） |
| `review_agent` | 只读审查真实 Diff、门禁、验证和回归报告 | 交付、回修或人工处理 |
| `request_approval` | 交付方案人工决策；拒绝进入有上限的回修循环 | 收尾 |
| `prepare_delivery` | 创建修复分支并按配置提交/推送 | `finalize_execution` |
| `finalize_execution` | 生成终态报告并收尾 | 结束 |

任务子图保留 10 个业务主节点；`retry_prepare` 是内部控制节点，不计入节点数。run 级主图只保留日志准备阶段和 `identify_carrier` 两个节点。10 个任务主节点保留了 Agent、安全隔离、资格占用、回归、审批、交付等必须独立审计的边界。

## 原子步骤目录与职责

| 原子步骤 | 层级 | 输出责任 |
|---|---|---|
| `discover_logs` | run | 保存实际日志查询条件 |
| `normalize_error` | run | 校验身份字段、拉取并规范化日志，写入代表问题产物 |
| `deduplicate_error` | run | 过滤重复与忽略项 |
| `identify_carrier` | run | 按 `(biztype, carrier)` 创建任务，并保留问题指纹 |
| `prepare_source_snapshot` | task | 保存目标源码基线及哈希产物 |
| `prepare_evidence` | task | 校验框架入口并准备有限源码证据 |
| `claim_repair_slot` | task | 定位通过后原子领取本轮资格 |
| `prepare_workspace` | task | 建立隔离工作区并恢复目标 carrier 基线 |
| `check_changes` | task | 校验路径、范围、AST、敏感信息并保存完整 Diff |
| `verify_issue` | task | 验证原始问题是否修复 |
| `run_regression` | task | 执行配置的准生产回归 |
| `request_approval` | task | 保存人工决策 |
| `prepare_delivery` | task | 生成修复分支与交付结果 |
| `finalize_execution` | task | 汇总该任务的步骤记录、状态和产物 |

Locate、Repair、Review Agent 仍分别是独立 Agent 子图，不与确定性步骤合并。

单步入口：`python -m app.workflow.steps.<step> --run-id <real-run-id> [--task-id <task-id>]`。只读取真实记录和 artifact；不存在样例输入或替代数据路径。

## 终态语义

`status` 记录节点执行状态，`result` 记录业务结论。定位/审查返回 `MANUAL` 或证据不足时，`finalize_execution` 必须将最终报告写为 `MANUAL`，不能因节点自身 succeeded 而误记为 FAILED 或 SUCCESS。人工介入不等于审批；审批只处理存在明确方案且处于 interrupt 的任务。

模型字段约定见 `docs/MODEL_AND_CONFIG_REFERENCE.md`，分支与终态审计见 `docs/WORKFLOW_AUDIT.md`。
