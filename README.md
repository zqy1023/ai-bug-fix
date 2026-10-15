# AI 异常日志自动修复工作流

项目按 `domain / application / workflow / agents / infrastructure / prompts` 分层。每个工作步骤是独立插件，输入、输出、执行函数和真实数据单步入口同目录维护。

## 主流程

```text
run 级（一次执行跑一遍）:
discover_logs -> normalize_error -> deduplicate_error -> identify_carrier
  -> task_dispatch（串行游标，每次只放行一个任务）
  -> 任务级子图（每个任务按 tasks 顺序串行执行）:
     prepare_source_snapshot -> prepare_evidence -> locate_agent
     -> claim_repair_slot -> prepare_workspace -> repair_agent
     -> check_changes -> verify_issue -> run_regression
     -> review_agent | request_approval -> apply_plan
     -> prepare_delivery -> finalize_execution
  -> task_collect（记下结论，推进游标）-> 回到 task_dispatch
```

任务之间严格串行：前一个任务走到 `finalize_execution` 并记完汇总后，游标才推进到下一个。
任务子图持有自己的状态（`WorkflowState`），父图只保留 run 级字段与结果汇总（`RunState`），
所以 `changed_files`、`repair`、`locate` 这类任务局部通道不会跨任务累积。

旧框架判断只看 `gathers/<Carrier>/main.py`：存在则按新框架处理；不存在且 `spiders/<Carrier>/` 存在则在证据预检后转人工，不调用 Locate 或 Repair。缺少两类实现时步骤明确失败。

## 结构

```text
app/
├── domain/                 业务模型、异常、政策、终态决策
├── application/            上下文、契约、用例服务、执行审计
├── workflow/
│   ├── graph.py            LangGraph run 级主图 + 任务子图
│   ├── routes.py           门禁路由
│   ├── state.py            RunState（父图）与 WorkflowState（任务子图）
│   ├── registry.py         步骤与 Agent 注册
│   ├── runner.py           单步骤记录执行器
│   └── steps/<step>/       SPEC、models、node、__main__
├── agents/{locate,repair,review}/  独立 Agent 子图
├── infrastructure/         ES、Git、文件系统、Claude、命令和记录适配器
├── prompts/                common.md + 每个 Agent 一份角色提示词
└── entrypoint.py           整图入口
```

## 真实数据单步调试

单步命令必须使用整图执行产生的真实 `run_id` 和任务 `task_id`。步骤从 `.ai-repair/runs/<run_id>/steps.json` 与 artifact 重建输入，不接收 fixture、stub 或手写问题数据；缺少通过的前置记录时直接拒绝。

```powershell
.venv\Scripts\python.exe -m app.workflow.steps.normalize_error --run-id <real-run-id>
.venv\Scripts\python.exe -m app.workflow.steps.prepare_evidence --run-id <real-run-id> --task-id <task-id>
.venv\Scripts\python.exe -m app.agents.repair --run-id <real-run-id> --task-id <task-id> --confirm-write
```

需要写入隔离区、执行配置命令或进行 Git 交付的单步，要求显式指定 `--confirm-write`。完整工作流会生成新的 run ID：

```powershell
.venv\Scripts\python.exe -m app.entrypoint --run-id <run-id>
```

运行记录目录通过 `AI_REPAIR_RUNTIME_DIR` 配置，默认位于项目根目录 `.ai-repair/`。
"# ai-bug-fix" 
