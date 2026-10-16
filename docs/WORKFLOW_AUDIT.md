# 工作流逻辑审计记录

## 业务不变量

1. 一轮执行由稳定的 `run_id` 标识，任务按 `identify_carrier` 输出顺序串行执行。
2. 自动修复资格以 `run_id + biztype + carrier` 为唯一键。同一轮同一运输商只领取一次资格，不按自然日重置。
3. 定位 Agent 只读；修复 Agent 的可写根目录只允许目标 `gathers/<carrier>`；审查 Agent 只读。
4. 共享层变更必须有结构化方案与明确的人工作出决定；审批通过后仍须重新执行门禁、验证和回归。
5. `StepRecord.status` 表示节点执行情况；`StepRecord.result` 表示业务结论。不能从节点执行成功推断业务成功。
6. `MANUAL` 是人工介入终态，不是变更审批，也不是成功。只有带待处理方案和审批中断的任务才进入审批中心。
7. 回归只在本地准生产 Git 工作区执行，不提交；交付步骤单独创建修复分支并提交，合并保持人工操作。

## 本轮审计发现与修正

### 严重：业务失败输出没有进入路由状态

根因：步骤执行器原先调用 `map_outcome(kind="ok")` 时没有传入本步的结构化输出和输出模型，成功执行的节点记录因此丢失业务结论；内部 `StepResult.status` 又固定返回 passed，导致部分 `passed=false` 的检查/验证/回归结果可能沿成功分支继续执行。

修正：执行器现在把实际输出和输出模型传给统一结论映射器。持久化记录仍区分节点执行状态与业务结论；图内路由状态则按 FAIL/FAILED、MANUAL/证据不足、SKIPPED 分别映射。即使业务结论是失败或人工介入，结构化输出仍回写状态，保证重试路由和共享层方案判断能读取到真实结论。

### 高优先级：人工介入被最终报告记成 FAILED

根因：`finalize_execution._delivery_status` 只对小写 `terminal_status == "manual"` 或旧框架任务返回 MANUAL，而定位/审查的业务结论实际记录在步骤 `result` 中，且通常是大写 `MANUAL`。结果是节点成功执行但业务结论为人工介入，最终 `reports/final.json` 却记为 FAILED。

修正：终态汇总先归一化业务结论大小写；只要终态或任何步骤结论是 `MANUAL` / `INSUFFICIENT_EVIDENCE`，最终报告记为 `MANUAL`。失败判断也使用归一化后的业务结论。

### 高优先级：Run 状态把人工介入误判为已完成

根因：`_derive_run_status` 只识别 FAILED/FAIL，没有将 MANUAL 纳入非成功终态。全部任务进入 MANUAL 时，Run 可能被展示成 completed/succeeded。

修正：任务终态含 MANUAL 时 Run 不再返回 completed；对外返回 `partial_failed`，前端显示为“部分失败 / 需关注”。执行摘要另外暴露 `manual_tasks`，不与失败数或待审批数混用。

### 高优先级：后台图外异常导致 Run 永久显示运行中

根因：后台执行器捕获图外异常后只写日志，没有向运行记录写入可查询的失败终态；当异常发生在步骤记录器之外时，工作台可能只能看到旧的 running 状态。

修正：启动和恢复后台任务发生未捕获异常时，写入 `reports/run_failure.json`；Run 查询将其归为 FAILED，详情页显示异常类型和服务端日志排查提示。报告不回显原始异常消息，避免泄漏令牌或连接串。

### 前端结论本地化

前端状态标签覆盖常见小写/大写状态、审查结论、验证状态和交付状态。任务结论、节点检查器和模型调用状态使用同一套中文状态映射；未知枚举保留原值，避免静默伪造翻译。

### Agent 审计与任务范围

模型调用列表支持按当前选中的任务过滤，再按 Agent 角色过滤。旧记录若没有公开事件，只展示实际存在的调用元数据，不生成虚构的对话或耗时。

## 必须持续验证的边界

- 对 `max_attempts=1/2/3/5` 分别验证最大实际修复调用次数；重试计数只由 `retry_prepare` 增加。
- 验证修复失败、检查失败、验证失败、回归失败、审查 FAIL、审查 MANUAL、审批拒绝、共享层审批后再次验证失败等分支的终态和重试次数。
- 验证审批拒绝不得继续进入 apply/delivery；过期或指纹不匹配的审批不得放行。
- 验证服务在节点异常退出时不会把 Run 永久留在 running；后台异常应落入可查询的失败记录。
- 验证 EventSource 在启用 Bearer Token 时的认证行为。浏览器原生 EventSource 不支持自定义 Authorization 头，当前应以轮询快照作为可靠兜底，或改用带认证头的 fetch-stream。
- 验证跨进程/跨容器的资格、审批恢复和取消信号共享同一个持久化 runtime 目录。
- 验证 Docker 中挂载的源仓库具备 Git 读写权限和必要的提交身份；默认禁止自动推送，远端合并仍由人工完成。

## 验收命令

```bash
python -m compileall -q app workbench config tests
pytest -q
cd frontend && npm install && npm run typecheck && npm run build
docker compose config
docker compose build
```

这些命令必须在实际部署环境执行并保存结果；未执行的项目不能标记为通过。

## Agent 事件轨迹与运行控制补充

- Provider 调用元数据写入 Run 级 `model_calls.jsonl`；有效公开事件同时追加到任务级 `tasks/<task_id>/audit/agent_events.jsonl`，每条记录带有 `model_call_id`、Agent 角色、尝试序号和事件序号。旧记录没有事件时只显示调用元数据，不回填伪造轨迹。
- 隐藏思考块、完整 SDK 消息对象、Prompt 正文不写入调试归档；终端输出保持摘要化，hook 调试文件限 5 MiB 并滚动一份。
- 暂停请求持久化到 `workbench-control.sqlite3`，在下一节点边界调用 LangGraph interrupt；继续清除暂停标志并复用原 thread_id。取消请求在安全边界进入 FINALIZE，后续不得回修、审批、回归或 Git 交付，最终报告应为 CANCELLED。
- 控制表升级必须为已有 SQLite 数据库补充 `pause_requested` 列，不能要求清空旧控制库。
