# 工作流生产可靠性与边界

本文记录当前主分支实现的可靠性约束、审计修正和仍需实际验收的边界。它不是测试通过声明。

## 已实现的约束

- **执行轮次资格**：`ExecutionEligibility` 使用 SQLite 唯一键与事务，键为 `run_id|biztype|carrier`。同一轮同一运输商只领取一次自动修复资格；不按自然日重置。
- **串行任务调度**：Run 级节点先发现、规范化、去重和识别任务，父图通过游标串行运行任务子图；任务局部状态不会跨任务累积。
- **重试语义**：业务重试通过 `retry_prepare` 统一递增 attempt。任务图不对写文件、审批和 Git 交付等副作用节点应用通用自动重试，避免重复提交/推送。
- **持久化恢复**：LangGraph 使用 SQLite checkpoint，`thread_id=run_id`。人工审批恢复必须使用相同 thread_id。
- **独立验证**：先做 Python 语法验证，再按可用输入进行 response 重放验证。验证输入通过结构化 JSON 传递，不将日志值拼接进 Python 源码。
- **确定性安全门禁**：改动范围、文件哈希、敏感信息、危险调用和语法由确定性门禁校验；模型声明的 changed_files 必须与真实 diff 对账。
- **审批安全**：审批内容绑定指纹，恢复时校验指纹；批准后仍执行最终范围门禁。共享层方案须明确授权。
- **准生产回归**：回归在本地 Git 工作区执行，不提交；检测测试是否意外改写工作区文件。
- **Agent 工具边界**：定位与审查只读，修复写入范围固定到目标 carrier；工具调用经过 SDK hook 检查路径和预算。
- **审计**：步骤记录分离执行状态与业务结论；模型调用记录按 Agent/任务记录可公开事件、工具调用和耗时，不记录隐藏推理链。
- **人工介入终态**：最终报告先判断 MANUAL/INSUFFICIENT_EVIDENCE 业务结论，避免将节点执行成功误当业务成功；Run 汇总不再把 MANUAL 任务归类为成功。

## 失败与人工介入语义

- `status=failed` 表示节点执行失败。
- `result=FAIL` 表示业务审查不通过，即使节点本身执行成功也不能算成功交付。
- `result=MANUAL` 表示自动流程安全停止，需要人工判断，不等于有方案待审批。
- `request_approval` 只用于存在明确方案且等待人工作决定的路径。
- Run 含 MANUAL 任务时标记为 `partial_failed`，并单独统计 `manual_tasks`，避免显示为全部成功。

## 仍需验收的边界

1. 对 `max_attempts=1/2/3/5` 验证最大实际修复调用次数和 attempt 计数。
2. 覆盖修复失败、检查失败、验证失败、回归失败、审查 FAIL/MANUAL、审批拒绝、共享层审批后复验失败。
3. 后台启动/恢复包装器已将图外异常写入 `reports/run_failure.json`，详情页展示异常类型和服务端日志提示；仍需故障注入验证异常写盘失败时的降级行为。
4. 验证启用 Bearer Token 后 SSE 的认证行为。原生 EventSource 不支持自定义 Authorization 头，轮询快照必须可用；若需实时推送，应改为带认证头的 fetch-stream。
5. 验证跨进程部署共享同一 runtime 目录；跨主机部署不能假设本地 SQLite/文件锁可直接成为分布式锁。
6. 验证 Docker 挂载的源仓库可写、Git 身份配置完整，且没有将凭据复制到镜像层。
7. 在实际环境执行 Python 测试、前端类型检查/构建和 Docker 构建。未执行的验收项保持未通过/未知。

## 验收命令

```bash
python -m compileall -q app workbench config tests
pytest -q
cd frontend && npm install && npm run typecheck && npm run build
docker compose config
docker compose build
```

