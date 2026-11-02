# 原方案逐项执行对照

基线：`aadbae9ea2ac4736a3a5aa3b2bb31e6b7dbcea9a`。保留原方案原文，以下逐条对应其表格操作。

“本地落实”表示代码检查、隔离回归或故障注入已覆盖该项；不表示线上承运商业务已恢复，也不表示分支已合并。具体测试结果、远端状态及适用限制见 README。

| 序号 | 原方案事项 | 本次处理 | 证据（tests/ 下，另注明前端/CI） |
| --- | --- | --- | --- |
| 1 | Run 与 Task 状态不一致 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 2 | 任务失败后的运行状态结算 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 3 | 任务取消后后台仍执行 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 4 | 任务关闭能力 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 5 | Run 级别任务统计 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 6 | 运行队列串行执行 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 7 | Worker 崩溃后的状态恢复 | 本地落实：生命周期、全局执行槽、只读投影、人工关闭；取消边界沿用现有受控停止机制 | test_lifecycle_worker_lease.py / test_cancel_stop.py / test_worker_timeout_settlement.py / test_workflow_remediation.py |
| 8 | Task 重试与 Checkpoint 更新不是原子操作 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 9 | Run 重试多存储状态一致性 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 10 | Run 恢复失败后的恢复能力 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 11 | 任意节点单步重试 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 12 | 单步重试报告归档 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 13 | Task 原生重跑 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 14 | 重试后的旧报告清理 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 15 | 重试失败后的状态回滚 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 16 | 重试过程中的旧结果污染 | 本地落实：持久化 retry_intent、归档移动日志、备份、Checkpoint op id、显式恢复及单步启动补偿 | test_retry_report_archival.py / test_step_retry_lifecycle.py / test_workflow_remediation.py |
| 17 | 平台生成文件进入审查范围 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 18 | 声明修改文件数与实际文件数不一致 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 19 | 重试后基线污染 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 20 | 新旧框架文件混合审查 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 21 | 未声明修改被错误判定 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 22 | Review Agent 与确定性检查职责重叠 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 23 | 审查失败后重新执行 | 本地落实：共享源码 diff 排除平台文件；只读基线、实际修改与声明、确定性规范检查 | test_agent_review_flow.py / test_workflow_audit_fixes.py / test_workflow_remediation.py |
| 24 | prepare_source_snapshot 长路径失败 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 25 | UnsafeOptionError | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 26 | prepare_delivery Clone 失败 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 27 | 分支提交失败却进入人工分析列 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 28 | 分支提交与 SQLite 记录不一致 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 29 | 空引用强制推送风险 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 30 | 重试工作区残留 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 31 | Git 分支重复创建 | 本地落实：固定 longpaths 克隆参数、实际错误链、回执恢复及远端 compare-and-swap | test_remote_branch_state.py / test_source_progress.py / test_workspace_worker_fencing.py / test_workflow_remediation.py |
| 32 | 旧 request_approval 路由残留 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 33 | 审批旧接口和恢复逻辑残留 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 34 | 单步执行仍限制旧审批节点 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 35 | 审批通过后后续任务不执行 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 36 | 审批通过后自动合并 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 37 | 审批驳回后的回退 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 38 | 审批驳回原因 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 39 | 人工处理关闭任务 | 本地落实：独立 final_approval；旧恢复入口停用；候选版本绑定、原因类别、回退队列、人工关闭 | test_routing_policy.py / test_agent_review_flow.py / test_workflow_remediation.py / browser_remediation.py |
| 40 | 初始验收条件缺少不可变约束 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 41 | 根因分析与验收条件耦合 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 42 | 技术验收与业务验收没有明确分层 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 43 | Review 未独立验证原始约束 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 44 | Agent 输出和平台最终判定混淆 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 45 | Agent 异常与业务失败混淆 | 本地落实：修补前冻结 AcceptanceSpec；技术与业务要求分开；Review 只读核对；平台计算最终结果 | test_agent_startup_outcome.py / test_analysis_disposition_review.py / test_agent_review_flow.py / test_workflow_remediation.py |
| 46 | 旧框架异常仍继续进入新框架分析 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 47 | 旧框架直接引用新框架的识别 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 48 | final.json 交付规则 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 49 | Carrier 自动修复范围 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 50 | 非允许范围的修改 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 51 | gather.py.track 根任务识别 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 52 | 任务唯一键 | 本地落实：沿用既有 carrier 归属、旧框架只读、允许目录及可信任务身份规则；补齐时间错误规则 | test_routing_policy.py / test_analysis_disposition_review.py / test_identify_carrier_selection.py / test_es_parse_message.py / test_workflow_remediation.py |
| 53 | 前端状态与后端生命周期不一致 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 54 | needsAttention() 等统计使用旧结果 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 55 | 取消请求显示为已停止 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 56 | Task / Run API 字段不一致 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 57 | 运行队列进度不完整 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 58 | 执行记录信息过多 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 59 | JSON 原始内容直接铺满页面 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 60 | 审批页面缺少代码上下文 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 61 | 页面状态与按钮可用性冲突 | 本地落实：生命周期优先、状态断言、后端操作权限、独立审批及同页原始异常/Review/真实 Diff；精简审计默认展示 | frontend/tests/status.mjs / test_task_outcomes.py / test_workflow_remediation.py / browser_remediation.py |
| 62 | RunState 与 Task SubGraph 边界 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 63 | TASK_DISPATCH → RUN_TASK → TASK_COLLECT | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 64 | Step 结果模型统一 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 65 | 状态枚举统一 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 66 | 结果枚举统一 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 67 | elapsed_ms 旧字段兼容 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 68 | change_plan=None | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 69 | 大型步骤产物 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 70 | Artifact API | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 71 | 旧 Pydantic 字段问题 | 本地落实：保留执行状态与业务判定分层；任务子图去掉批次容器；duration 兼容；大日志落产物；限制并审计 Artifact API | test_unified_fixer_contract.py / test_routing_policy.py / test_single_anomaly_e2e.py / test_workflow_remediation.py |
| 72 | PR #28 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 73 | PR #29 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 74 | PR #30 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 75 | 重试报告归档 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 76 | 单步重试 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 77 | 取消与停止 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 78 | Worker Lease | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 79 | 审批恢复 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 80 | 交付完整性 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |
| 81 | 前后端契约 | 本地落实：全量后端、故障注入、多进程互斥、真实 Git、API 与浏览器测试纳入 CI | 全部 tests/ / frontend/tests/status.mjs / .github/workflows/ci.yml |

## 原文表格以外的操作

- Redis 连接错误与 cache_controller 默认排除规则：核验配置默认值与清洗优先级；保留既有排除规则。
- Almex、Spreetail、DPDAT 历史样例：新增参数化回归，保留完整错误并加载时间/WAF 规则。Mothership 正文识别沿用既有回归。未声称这些承运商的线上请求已通过。
- final.json 与 BrazilLogin.py：实际源码 diff 回归确认只计业务源码；平台 final.json 不计入实际改动。
- 知识中心、knowledge_case_usage、虚假工作区切换、固定用户、无数据系统状态、假通知入口：重新搜索前后端；移除仍残留的静态用户与空交互。禁用按钮仅保留确有后端权限/进行中状态的动作。
- PR #30：内容已包含在本批次，并扩展覆盖旧交付、暂停和待处理统计；原 PR 仍保持开放，等待人工决定合并顺序。
- 三个旧远端分支：均已建立远端归档标签后按提交号 CAS 删除，见 branch-cleanup.json。durable-run-lease 未被错误标记为单独已合并。
- 人工合并：工作流、审批接口和测试均不执行 merge；本整改分支也交由人工合并。

表格对照共 81 项。
