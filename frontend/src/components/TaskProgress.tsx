import type { TaskSummary, WorkflowStage } from '../lib/types';
import { WORKFLOW_STAGES } from '../lib/workflow.generated';
import { ProgressBar } from './ui';

export function taskStages(task: TaskSummary, stages: WorkflowStage[] = WORKFLOW_STAGES) {
  const terminal = ['CHANGED_DELIVERED', 'UNCHANGED_DELIVERED', 'FAILED', 'SKIPPED'].includes(task.result);
  return stages.map((stage) => {
    const steps = task.steps?.length ? task.steps : Object.entries(task.step_statuses ?? {}).map(([name, status]) => ({ name, status }));
    const records = steps.filter((step) => stage.members.includes(step.name) || step.name === stage.key);
    const current = task.current_step || task.lifecycle?.current_step;
    const terminalStep = task.report?.terminal_step || task.lifecycle?.terminal_step;
    const failed = records.some((step) => step.status === 'failed') ||
      (task.result === 'FAILED' && !!terminalStep && (stage.members.includes(terminalStep) || stage.key === terminalStep));
    const active = !terminal && (records.some((step) => ['running', 'waiting'].includes(step.status)) || (!!current && (stage.members.includes(current) || stage.key === current)));
    const done = records.some((step) => step.name === stage.key && ['succeeded', 'skipped'].includes(step.status)) ||
      stage.members.every((name) => records.some((step) => step.name === name && ['succeeded', 'skipped'].includes(step.status)));
    const state = failed ? 'fail' : done ? 'done' : active ? 'active' : terminal ? 'skipped' : 'pending';
    return { ...stage, state, label: failed ? '失败' : done ? '已完成' : active ? (task.lifecycle?.status === 'paused' ? '已暂停' : '执行中') : terminal ? '未执行' : '未开始' };
  });
}

export function CompactTaskProgress({ task, stages }: { task: TaskSummary; stages?: WorkflowStage[] }) {
  const rows = taskStages(task, stages);
  const completed = rows.filter((stage) => ['done', 'skipped', 'fail'].includes(stage.state)).length;
  return <div className="task-progress-compact" aria-label={`${completed}/${rows.length} 阶段已结束`}>
    <span className="cell-muted">{completed} / {rows.length} 阶段</span>
    <ProgressBar pct={rows.length ? completed / rows.length * 100 : 0} />
  </div>;
}

export function TaskProgress({ task, stages }: { task: TaskSummary; stages?: WorkflowStage[] }) {
  const rows = taskStages(task, stages);
  const completed = rows.filter((stage) => ['done', 'skipped', 'fail'].includes(stage.state)).length;
  return (
    <section className="task-progress" aria-label="任务执行进度">
      <div className="row between">
        <strong>执行进度</strong><span className="cell-muted">{completed} / {rows.length} 阶段已结束</span>
      </div>
      <ProgressBar pct={rows.length ? (completed / rows.length) * 100 : 0} />
      <ol className="task-stage-list">
        {rows.map((stage, index) => <li className={`task-stage ${stage.state}`} key={stage.key}>
          <span className="stage-index">{stage.state === 'done' ? '✓' : index + 1}</span>
          <div><strong>{stage.title}</strong><span>{stage.label}</span></div>
        </li>)}
      </ol>
    </section>
  );
}
