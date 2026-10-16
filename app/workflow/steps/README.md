# 工作步骤

目录名、`SPEC.name`、步骤记录名与调试模块名必须一致。每个步骤只声明一个输入模型、一个输出模型和 `run(ctx, data)`，不在步骤之间复制状态或合并执行。

| 步骤                        | 层级   | 输入来源                             | 输出责任                                        |
|---------------------------|------|----------------------------------|---------------------------------------------|
| `discover_logs`           | run  | 已配置 ES                           | 把可重放查询随步骤记录写入 `steps.json`（不预扫）       |
| `normalize_error`         | run  | `steps.json` 里 discover 的查询         | 校验身份字段、流式拉取并去重，计数和代表问题写入本步记录         |
| `deduplicate_error`       | run  | `steps.json` 里 normalize 的记录      | 汇总过滤结果                                        |
| `identify_carrier`        | run  | `steps.json` 里 normalize 的记录      | 以 `(biztype, carrier)` 唯一建任务，写本步记录 |
| `prepare_source_snapshot` | task | 源仓库、目标 carrier                   | 保存目标源码基线及哈希 artifact                        |
| `prepare_evidence`        | task | 真实问题、源码快照                        | 校验框架入口并准备候选源码证据；纯旧框架转人工                     |
| `locate_agent`            | task | 问题、证据、源码只读范围                     | 仅有可核验根因证据时放行                                |
| `claim_repair_slot`       | task | 已定位的问题指纹                         | 定位通过后原子领取本轮处理资格，避免无效任务占用资格 |
| `prepare_workspace`       | task | 源仓库、源码快照、任务                      | 建立隔离工作区并恢复目标 carrier 基线                     |
| `repair_agent`            | task | 问题、证据、基线                         | 在目标 carrier 写入最小修改                          |
| `check_changes`           | task | 修改清单、快照                          | 校验路径、范围、AST 和敏感信息                           |
| `verify_issue`            | task | 改动、问题指纹                          | 验证原始问题是否修复                                  |
| `run_regression`          | task | 真实隔离工作区、配置命令                     | 执行准生产回归                                     |
| `review_agent`            | task | 改动与门禁证据                          | 只读审查                                        |
| `request_approval`        | task | 方案与授权信息                          | 保存人工决策                                      |
| `apply_plan`              | task | 已批准方案                            | 唯一共享范围落盘通道                                  |
| `prepare_delivery`        | task | 已验证改动                            | 生成交付结果                                      |
| `finalize_execution`      | task | 该任务全部步骤记录                        | 生成终态报告                                      |

单步入口：`python -m app.workflow.steps.<step> --run-id <real-run-id> [--task-id <task-id>]`。只读取真实记录和 artifact；不存在样例输入或替代数据路径。

## 终态语义

`status` 记录节点执行状态，`result` 记录业务结论。定位/审查返回 `MANUAL` 或证据不足时，`finalize_execution` 必须将最终报告写为 `MANUAL`，不能因节点自身 succeeded 而误记为 FAILED 或 SUCCESS。人工介入不等于审批；审批只处理存在明确方案且处于 interrupt 的任务。

模型字段约定见 `docs/MODEL_AND_CONFIG_REFERENCE.md`，分支与终态审计见 `docs/WORKFLOW_AUDIT.md`。
