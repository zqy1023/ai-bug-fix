# AI 异常自动修复工作台

当前协议为 **v8**。工作流只向用户暴露八个业务阶段，后端组合步骤和 Agent 内部工具属于实现细节，不再作为独立业务节点展示。

## 八个业务阶段

```text
准备上下文
→ 分析模型
→ 领取资格
→ 修复模型
→ 审查模型
→ 生成报告
→ 提交本地、远程分支
→ 人工审批
```

对应节点名：

```text
prepare_context
locate_agent
claim_repair_slot
repair_agent
review_agent
generate_report
submit_changes
request_approval
```

`finalize_execution` 是内部终态汇总节点，`retry_prepare` 是内部恢复控制节点，两者不属于公开业务阶段。

## 执行规则

- `prepare_context` 组合执行分支预检、源码快照、隔离工作区和定位证据准备。
- `locate_agent` 一次完成异常归因、旧框架判断、验收条件、修复前基线复现和修复权限判断。
- 没有原始 `response` 时记录“业务未验证”，继续进入修复、审查、报告和交付链路。
- `repair_agent` 只按策略允许的范围写入工作区；共享层、旧框架或超范围修改会转分析报告或人工处理。
- `review_agent` 内部完成变更范围、语法、原问题重放、真实请求回归和模型审查，不创建独立验证节点。
- `generate_report` 汇总分析、修复、审查和验证结果。
- `submit_changes` 组合准备本地候选并按策略推送同一提交；关闭权限时写入 `skipped` 记录并交付报告。
- `request_approval` 只处理正式候选的人工保留或打回，不自动合并代码。

## 代码结构

| 目录 | 职责 |
| --- | --- |
| `app/agents/locate` | 分析模型、归因、验收和修复前基线能力 |
| `app/agents/repair` | 受权限策略约束的源码修复 |
| `app/agents/review` | 审查模型及其内部验证工具 |
| `app/workflow/definitions.py` | 八个公开阶段、组合步骤和路由 |
| `app/workflow/graph.py` | LangGraph 图编译和任务串行分发 |
| `app/workflow/agent_adapter.py` | Agent 输入输出适配和审计 |
| `app/workflow/runner.py` | 步骤记录、状态和恢复执行 |
| `app/workflow/handoff.py` | 交接产物、状态归属和失效传播 |
| `app/workflow/validation.py` | 验证计划、证据汇总和统一决策 |
| `app/workflow/steps/run` | 批次级日志、规范化、去重和承运商识别插件 |
| `app/workflow/steps/context` | 准备上下文阶段的源码、工作区和证据插件 |
| `app/workflow/steps/access` | 修复资格领取与释放 |
| `app/workflow/steps/delivery` | 本地候选和远程分支提交 |
| `app/workflow/steps/report` | 生成报告与内部收尾汇总 |
| `app/workflow/steps/approval` | 人工审批节点 |
| `workbench/api.py` | `/api/v1` 工作台接口 |
| `workbench/projections.py` | 后端阶段和任务详情投影 |
| `frontend/src/lib/workflow.generated.ts` | 从后端生成的前端契约 |
| `frontend/src/features/task` | 任务工作台、阶段导航和步骤详情 |

已删除的旧节点包括：`define_acceptance`、`reproduce_baseline`、`prepare_analysis`、`decide_validation`。验收与基线代码位于 `app/agents/locate/analysis_checks.py`，由分析模型内部调用。插件不再维护逐目录 `__main__.py` 包装脚本，统一通过应用层执行器调度。

## Agent 策略

批次或任务可以配置：

- 分析、修复、审查模型选择；
- 是否分析旧框架、是否复现、是否生成验收条件；
- 旧框架修复、共享目录修复、新文件和代码退役权限；
- 修复后验证、真实请求回归和自动回修；
- 本地提交和远程推送权限。

策略在准备阶段冻结并写入任务产物。权限关闭时流程继续生成报告，并明确记录跳过原因。

## 配置来源与模型目录

运行配置只有一条解析链：进程环境变量优先，其次是项目根目录 `.env`，最后使用
`config/settings.py` 中的代码默认值。`Settings` 只保存默认值，不在模块导入时读取环境；
`RuntimeConfig` 是 `load_config()` 解析后的唯一运行快照。Agent 自身的预算和工具权限仍由
各自的 `app/agents/*/config.json` 管理，并接受对应 `AI_REPAIR_AGENT_*` 环境变量覆盖。

模型策略保存 provider 的真实 `model_id`。工作台启动配置会请求 provider 的 `/v1/models`
目录，只展示当前认证下返回的可用模型；后端在启动运行和保存任务策略时会再次校验。空模型值
表示使用 `ANTHROPIC_MODEL`，不会再使用 `default/haiku/sonnet/opus` 这类本地别名。

## 前端展示

- 主页面只展示八个业务阶段、状态和关键结论。
- 步骤输入、输出、上下文、产物和完整错误进入详情抽屉。
- 只有 `locate_agent`、`repair_agent`、`review_agent` 显示提示词、对话流、模型输出和工具调用。
- 原始异常单独展示完整 `message`、`error`、`raw_error`、`req` 和 `response`。
- 阶段列表优先使用后端 `stages`，前端生成契约只作为接口缺失时的回退。

## 开发与验证

后端：

```powershell
.venv/Scripts/python.exe -m compileall -q app workbench
.venv/Scripts/python.exe -m pytest -q tests
```

前端：

```powershell
npm --prefix frontend run build
```

前端契约生成：

```powershell
.venv/Scripts/python.exe -m app.workflow.frontend_contracts
```

后端入口为 `server.py`，前端入口为 `frontend/`。运行记录位于 `runtime/`，不纳入源码重构和版本提交。
