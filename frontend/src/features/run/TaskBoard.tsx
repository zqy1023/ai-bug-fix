import type { BoardTask } from '../../lib/types';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { EmptyState } from '../../components/EmptyState';

type Column = { key: string; title: string; hint: string; tasks: BoardTask[] };

function columnOf(task: BoardTask): string {
  const result = String(task.result || '').toUpperCase();
  if (task.approval_pending || result === 'WAITING_APPROVAL') return 'waiting';
  if (['FAILED', 'FAIL', 'REJECTED'].includes(result)) return 'failed';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result)) return 'manual';
  if (['SUCCESS', 'SUCCEEDED', 'DELIVERED'].includes(result)) return 'success';
  return 'running';
}

const COLUMN_META = [
  { key: 'waiting', title: '待交付审批', hint: '唯一需要人工决策' },
  { key: 'failed', title: '失败', hint: '需检查错误与产物' },
  { key: 'running', title: '运行中 / 排队', hint: '自动链路推进中' },
  { key: 'manual', title: '分析报告', hint: '无需系统内操作' },
  { key: 'success', title: '自动交付', hint: '分支已产出待合并' },
];

export function TaskBoard({ tasks, onOpen }: { tasks: BoardTask[]; onOpen: (task: BoardTask) => void }) {
  if (!tasks.length) {
    return <EmptyState title="本轮尚未产生任务" description="任务在 identify_carrier 之后进入任务板。" compact />;
  }
  const columns: Column[] = COLUMN_META.map((meta) => ({
    ...meta,
    tasks: tasks.filter((task) => columnOf(task) === meta.key),
  }));

  return (
    <div className="task-board">
      {columns.map((column) => (
        <section key={column.key} className={`board-column board-${column.key}`}>
          <header className="board-column-head">
            <span className="board-column-title">{column.title}</span>
            <span className="board-column-count">{column.tasks.length}</span>
          </header>
          <p className="board-column-hint">{column.hint}</p>
          <div className="board-column-body">
            {column.tasks.length === 0 && <span className="board-empty">无任务</span>}
            {column.tasks.map((task) => (
              <button key={task.task_id} type="button" className="task-card" onClick={() => onOpen(task)}>
                <div className="task-card-head">
                  <strong title={task.carrier}>{task.carrier || '未识别运输商'}</strong>
                  <StatusBadge kind="task" value={task.result} compact />
                </div>
                <div className="task-card-meta">
                  <span title={task.biztype}>{task.biztype || '未知业务类型'}</span>
                  {task.service_name && <span className="task-card-service">{task.service_name}</span>}
                </div>
                <div className="task-card-foot">
                  <span>{task.steps_count} 步 · {formatDuration(task.duration_ms)}</span>
                  {(task.current_step || task.terminal_step) && (
                    <code>{['RUNNING', 'RETRYING', 'PENDING'].includes(String(task.result).toUpperCase()) ? '当前 ' : '终态 '}{task.current_step || task.terminal_step}</code>
                  )}
                  {(task.attempt ?? 0) > 1 && <span>第 {task.attempt} 次尝试</span>}
                </div>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
