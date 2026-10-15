# 应用结构

```text
app/
├── domain/                  models.py, errors.py, policies.py, decisions.py
├── application/             context.py, contracts.py, services.py, audit.py
├── workflow/                graph.py, routes.py, registry.py, runner.py, steps/
├── agents/                  common.py, policy.py, locate/, repair/, review/
├── infrastructure/          es/, git/, filesystem/, claude/, commands/, records/
├── prompts/                 common.md, locate.md, repair.md, review.md, loader.py, renderer.py
└── entrypoint.py
```

每个 `workflow/steps/<step>/` 只包含该步骤的契约、执行器和 CLI。运行图直接连接这些插件，不使用隐藏的组合步骤。

单步 CLI 只接受真实 `run_id`，任务级步骤还需 `task_id`；输入只从已通过的前置记录和运行 artifact 重建。不存在 fixture、stub 或伪造调试 ID。

```powershell
python -m app.workflow.steps.<step> --run-id <real-run-id> [--task-id <task-id>]
python -m app.agents.<role> --run-id <real-run-id> --task-id <task-id>
```
