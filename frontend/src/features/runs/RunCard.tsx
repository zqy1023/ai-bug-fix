import { RightOutlined } from '@ant-design/icons';
import type { RunSummary } from '../../lib/types';
import { formatDuration, formatTime, shortId } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { RunProgressHeatmap } from '../../components/RunProgressHeatmap';

export function RunCard({ run, onOpen }: { run: RunSummary; onOpen: () => void }) {
  const running = Math.max(
    0,
    run.task_count - run.succeeded_tasks - run.failed_tasks - run.manual_tasks - run.waiting_tasks,
  );
  const startedAt = new Date(run.started_at).getTime();
  const finished = run.finished_at
    ? new Date(run.finished_at).getTime() - startedAt
    : Math.max(0, Date.now() - startedAt);

  return (
    <button type="button" className="run-card" onClick={onOpen}>
      <div className="run-card-head">
        <span className="run-id" title={run.run_id}>run:{shortId(run.run_id, 10)}</span>
        <StatusBadge kind="run" value={run.status} compact />
        <span className="run-card-time">{formatDuration(finished)}</span>
      </div>
      <div className="run-card-meta">
        <span>{run.task_count} 个任务</span>
        <span className="dot-sep">·</span>
        <span>{run.succeeded_tasks} 自动 / {run.manual_tasks} 报告</span>
        {run.waiting_tasks > 0 && <span className="run-card-waiting">{run.waiting_tasks} 待审批</span>}
        {run.status === 'running' && run.task_count === 0 && (
          <span className="run-card-phase">启动中</span>
        )}
      </div>
      <RunProgressHeatmap
        succeeded={run.succeeded_tasks}
        waiting={run.waiting_tasks}
        manual={run.manual_tasks}
        failed={run.failed_tasks}
        running={running}
        total={run.task_count}
      />
      <div className="run-card-foot">
        <span>{run.operator ?? '系统'} · {formatTime(run.started_at)}</span>
        <RightOutlined />
      </div>
    </button>
  );
}
