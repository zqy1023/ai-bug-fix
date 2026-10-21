import { useMemo } from 'react';
import type { RunDetail, StepRecord, StepStatus } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';
import { shortId } from '../../lib/format';

type NodeSpec = {
  key: string;
  title: string;
  detail: string;
  members: string[];
  status: StepStatus;
  metric?: string;
};

const TERMINAL_RUN = new Set(['succeeded', 'partial_failed', 'failed', 'cancelled']);

function pick(steps: StepRecord[], names: string[]): StepStatus {
  const found = names.map((name) => steps.find((step) => step.name === name)).filter(Boolean) as StepRecord[];
  if (!found.length) return 'pending';
  if (found.some((step) => step.status === 'failed')) return 'failed';
  if (found.some((step) => step.status === 'running')) return 'running';
  if (found.some((step) => step.status === 'waiting')) return 'waiting';
  return found.every((step) => step.status === 'succeeded' || step.status === 'skipped') ? 'succeeded' : 'pending';
}

/** Run 级拓扑：静态图结构 + 真实步骤状态，只用于观察，不可编辑。 */
export function RunGraph({ run }: { run: RunDetail }) {
  const nodes = useMemo<NodeSpec[]>(() => {
    const steps = run.steps ?? [];
    const tasks = run.tasks ?? [];
    const taskStates = tasks.map((task) => String(task.result || '').toUpperCase());
    const terminal = run.terminal ?? TERMINAL_RUN.has(run.status);
    const tasksRunning = taskStates.some((state) => ['RUNNING', 'RETRYING', 'PENDING'].includes(state));
    const tasksWaiting = tasks.some((task) => task.approval_pending || String(task.result).toUpperCase() === 'WAITING_APPROVAL');
    const tasksPaused = taskStates.some((state) => state === 'PAUSED');
    const tasksFailed = taskStates.some((state) => ['FAILED', 'FAIL', 'REJECTED'].includes(state));

    // checkpoint.next_nodes 是“下一步要执行的节点”，用它判断图是否真的推进到了
    // 任务派发或汇总阶段；不能因为有 steps/tasks 就把汇总节点显示成运行中。
    const nextNodes = run.checkpoint?.next_nodes ?? [];
    const atSourceSync = nextNodes.includes('sync_source_repo');
    const atPrepare = nextNodes.includes('discover_and_normalize');
    const atIdentify = nextNodes.includes('identify_carrier');
    const atTask = nextNodes.some((name) => name === 'task_dispatch' || name === 'run_task');
    const atCollect = nextNodes.includes('task_collect');

    const sourceSyncRecord = steps.find((step) => step.name === 'sync_source_repo');
    const sourceSyncStatus: StepStatus = sourceSyncRecord
      ? sourceSyncRecord.status
      : terminal || steps.length > 0 ? 'skipped' : atSourceSync ? 'running' : 'pending';
    const prepareStatus = pick(steps, [
      'discover_and_normalize', 'discover_logs', 'normalize_error', 'deduplicate_error',
    ]);
    const identifyStatus = pick(steps, ['identify_carrier']);

    const taskStatus: StepStatus = tasksWaiting || tasksPaused ? 'waiting'
      : tasksRunning ? 'running'
        : tasks.length ? (tasksFailed ? 'failed' : 'succeeded')
          : terminal ? 'skipped'
            : atTask ? 'running'
              : atCollect ? 'skipped' : 'pending';

    const collectStatus: StepStatus = terminal
      ? (run.status === 'succeeded' ? 'succeeded' : run.status === 'cancelled' ? 'skipped' : 'failed')
      : atCollect ? 'running' : 'pending';

    return [
      {
        key: 'source_sync', title: '同步源仓库', detail: 'sync_source_repo',
        members: ['sync_source_repo'],
        status: sourceSyncStatus === 'pending' && atSourceSync ? 'running' : sourceSyncStatus,
      },
      {
        key: 'prepare', title: '日志与规范化', detail: 'discover_and_normalize',
        members: ['discover_and_normalize', 'discover_logs', 'normalize_error', 'deduplicate_error'],
        status: prepareStatus === 'pending' && atPrepare ? 'running' : prepareStatus,
        metric: run.run_lifecycle?.progress?.step === 'normalize_error'
          ? `已扫描 ${(run.run_lifecycle.progress.counts.es_hits ?? 0).toLocaleString()} 条日志` : undefined,
      },
      {
        key: 'identify', title: '识别运输商', detail: 'identify_carrier',
        members: ['identify_carrier'],
        status: identifyStatus === 'pending' && atIdentify ? 'running' : identifyStatus,
        metric: tasks.length ? `${tasks.length} 个任务` : undefined,
      },
      {
        key: 'run_task', title: '任务子图执行', detail: 'task_dispatch → run_task',
        members: [],
        status: taskStatus,
        metric: tasks.length
          ? `${tasks.filter((task) => task.approval_pending).length} 待审批 / ${tasks.length}`
          : undefined,
      },
      {
        key: 'collect', title: '汇总与终态', detail: 'task_collect → finalize',
        members: [],
        status: collectStatus,
      },
    ];
  }, [run]);

  const nextNodes = run.checkpoint?.next_nodes ?? [];
  const currentNode = nextNodes[0] ?? '';
  const activeKey = nextNodes.includes('sync_source_repo') ? 'source_sync'
    : nextNodes.includes('discover_and_normalize') ? 'prepare'
      : nextNodes.includes('identify_carrier') ? 'identify'
        : nextNodes.includes('task_collect') ? 'collect'
          : nextNodes.some((name) => name === 'task_dispatch' || name === 'run_task') ? 'run_task'
            : '';

  return (
    <section className="run-graph" aria-label="执行拓扑">
      <header className="run-graph-head">
        <h3>执行拓扑</h3>
        <span>
          静态图结构 · 状态来自步骤记录与 checkpoint
          {currentNode && <> · 下一节点 <code>{currentNode}</code></>}
          {run.checkpoint?.checkpoint_id && <> · checkpoint {shortId(run.checkpoint.checkpoint_id, 10)}</>}
        </span>
      </header>
      <ol className="run-graph-flow">
        {nodes.map((node, index) => (
          <li
            key={node.key}
            className={`graph-node status-${node.status}` + (activeKey === node.key ? ' is-current' : '')}
          >
            <div className="graph-node-head">
              <span className="graph-node-index">{index + 1}</span>
              <strong>{node.title}</strong>
              <StatusBadge kind="step" value={node.status} compact />
            </div>
            <code className="graph-node-detail">{node.detail}</code>
            {node.metric && <span className="graph-node-metric">{node.metric}</span>}
            {index < nodes.length - 1 && <span className="graph-connector" aria-hidden />}
          </li>
        ))}
      </ol>
    </section>
  );
}
