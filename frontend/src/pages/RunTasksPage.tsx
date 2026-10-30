import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatBatchTime, formatDuration, durationFrom, relativeTime, shortId } from '../lib/format';
import { attentionCount, isRunActive, runProgress } from '../lib/status';
import { useLiveClock } from '../lib/hooks';
import {
  Card,
  CardBody,
  CardHead,
  ErrorBox,
  Kpi,
  PageHeader,
  ProgressBar,
  RunStatusBadge,
  Skeleton,
  Tabs,
  TaskStatusBadge,
  Verdict,
  useToast,
} from '../components/ui';
import type { RunDetail, StepRecord, WorkflowStage } from '../lib/types';
import { CompactTaskProgress } from '../components/TaskProgress';
import { LogScanProgress } from '../components/LogScanProgress';

const RUN_STAGES: WorkflowStage[] = [
  { key: 'source', title: '同步源码', members: ['sync_source_repo'], group: false },
  { key: 'logs', title: '整理异常日志', members: ['discover_logs', 'normalize_error', 'deduplicate_error'], group: true },
  { key: 'identify', title: '识别全部任务', members: ['identify_carrier'], group: false },
];

export function RunTasksPage() {
  const { runId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const category = params.get('category') || 'all';
  const keyword = params.get('keyword') || '';
  const [search, setSearch] = useState(keyword);
  const [page, setPage] = useState(1);
  const [pauseRequested, setPauseRequested] = useState(false);
  const queryClient = useQueryClient();
  const { push } = useToast();
  const navigate = useNavigate();

  const run = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), enabled: Boolean(runId) });
  const board = useQuery({
    queryKey: ['board', runId, category, keyword, page],
    queryFn: () => api.board(runId, { page, page_size: 20, category, keyword }),
    enabled: Boolean(runId),
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === runId ? previous : undefined,
  });
  useLiveClock(isRunActive(run.data?.status));

  const detail = run.data;
  const summary = board.data?.summary ?? detail;
  const tasks = board.data?.tasks ?? [];
  const counts = board.data?.counts ?? {};
  const allTotal = board.data?.all_total ?? summary?.task_count ?? 0;
  const filteredTotal = board.data?.total ?? allTotal;
  const pages = Math.max(1, Math.ceil(filteredTotal / 20));
  const currentPage = board.data?.page ?? page;
  useEffect(() => { setPage(1); }, [runId, category, keyword]);
  useEffect(() => {
    if (board.data?.page && !board.isPlaceholderData && board.data.page !== page) setPage(board.data.page);
  }, [board.data, board.isPlaceholderData, page]);

  const setFilter = (nextCategory: string, nextKeyword = keyword) => {
    setPage(1);
    setParams((current) => {
      if (nextCategory && nextCategory !== 'all') current.set('category', nextCategory);
      else current.delete('category');
      if (nextKeyword) current.set('keyword', nextKeyword);
      else current.delete('keyword');
      return current;
    });
  };

  const action = useMutation({
    mutationFn: async (kind: 'pause' | 'resume' | 'retry' | 'cancel') => {
      if (kind === 'pause') return api.pauseRun(runId);
      if (kind === 'resume') return api.resumeRun(runId);
      if (kind === 'retry') return api.retryRun(runId);
      return api.cancelRun(runId);
    },
    onSuccess: (_data, kind) => {
      if (kind === 'pause') {
        setPauseRequested(true);
        push('已请求暂停', '当前节点完成或到达安全边界后生效');
      } else if (kind === 'cancel') {
        push('已请求取消', '当前节点收尾后停止，后续任务不再生成');
      } else {
        push('操作已提交');
      }
      void queryClient.invalidateQueries({ queryKey: ['run', runId] });
      void queryClient.invalidateQueries({ queryKey: ['board', runId] });
    },
    onError: (error) => push('操作失败', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  const stageStatus = useMemo(() => [
    ...buildStageStatus(detail?.steps ?? [], RUN_STAGES),
    { key: 'tasks', title: '串行执行任务', state: detail?.terminal ? 'done' : detail?.task_count ? 'active' : '' },
  ], [detail]);
  const visibleStages = stageStatus.filter((stage) => stage.title || stage.key);

  useEffect(() => {
    if (detail && (detail.status === 'paused' || !isRunActive(detail.status))) setPauseRequested(false);
  }, [detail]);

  if (run.isLoading && !detail) {
    return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  }
  if (run.error || !detail) {
    return <Card><ErrorBox error={run.error ?? new Error('批次不存在')} retry={() => void run.refetch()} /></Card>;
  }

  const progress = runProgress(summary ?? detail);
  const actions: NonNullable<RunDetail['actions']> =
    detail.actions ?? ({} as NonNullable<RunDetail['actions']>);
  const active = isRunActive(detail.status);
  const canPause = actions.pause?.enabled;
  const canResume = actions.resume?.enabled;
  const canCancel = actions.cancel?.enabled;
  const pending = summary?.pending_tasks ?? counts.pending ?? 0;
  const running = summary?.running_tasks ?? counts.active ?? 0;

  return (
    <>
      <PageHeader
        eyebrow={`Run / ${shortId(runId, 8)}`}
        title={<>批次 <span className="mono">{shortId(runId, 8)}</span></>}
        meta={
          <>
            {detail.task_count} 个任务
            <span className="dot">·</span>
            {attentionCount(detail)} 需处理
            <span className="dot">·</span>
            {formatBatchTime(detail.started_at)}
          </>
        }
        actions={
          <>
            {canPause || pauseRequested ? (
              <button
                className="btn"
                disabled={action.isPending || pauseRequested}
                onClick={() => action.mutate('pause')}
              >
                {pauseRequested ? '暂停中…' : action.isPending ? '提交中…' : '暂停'}
              </button>
            ) : null}
            {canResume ? <button className="btn" disabled={action.isPending} onClick={() => action.mutate('resume')}>恢复</button> : null}
            {actions.retry?.enabled ? <button className="btn" disabled={action.isPending} onClick={() => action.mutate('retry')}>重试</button> : null}
            {canCancel ? (
              <button
                className="btn danger"
                disabled={action.isPending}
                onClick={() => {
                  if (window.confirm('取消批次会停止后续任务，已交付的结果保留。确定取消？')) action.mutate('cancel');
                }}
              >
                取消
              </button>
            ) : null}
            <Link className="btn primary" to="/runs?new=1">新建批次</Link>
          </>
        }
      />

      <Verdict
        tone={runTone(detail)}
        icon={active ? '●' : detail.status === 'succeeded' ? '✓' : '!'}
        title={runVerdictTitle(detail)}
        desc={
          pauseRequested
            ? '已请求暂停，等待当前节点完成或到达安全边界；不会中断正在执行的模型调用。'
            : detail.run_error?.message ||
              detail.run_lifecycle?.terminal_reason ||
              `${progress.done} / ${progress.total} 完成 · ${attentionCount(detail)} 个需处理`
        }
        actions={<span className="cell-muted">{progress.pct}%</span>}
      />

      <Tabs
        active="tasks"
        items={[
          { key: 'tasks', label: '任务', count: allTotal, href: `/runs/${runId}` },
          { key: 'records', label: '记录', href: `/runs/${runId}/records` },
          { key: 'artifacts', label: '产物', href: `/runs/${runId}/artifacts` },
        ]}
      />

      <div className="kpis">
        <Kpi value={detail.task_count} label="任务总数" />
        <Kpi value={pending} label="未开始" />
        <Kpi value={running} label="执行中" />
        <Kpi value={summary?.changed_delivered_tasks ?? 0} label="已改动交付" tone="ok" />
        <Kpi value={summary?.unchanged_delivered_tasks ?? 0} label="无改动交付" tone="info" />
        <Kpi value={summary?.skipped_tasks ?? 0} label="已跳过" />
        <Kpi value={summary?.failed_tasks ?? 0} label="失败" tone="danger" />
      </div>

      <section className="run-progress" aria-label="批次执行进度">
        <div className="row between">
          <strong>{progress.done} <span className="cell-muted">/ {progress.total} 个任务已结束</span></strong>
          <span className="mono">{progress.pct}%</span>
        </div>
        <ProgressBar pct={progress.pct} tone={attentionCount(detail) ? 'warn' : undefined} />
        <div className="row between cell-muted">
          <span>{running} 个执行中 · {pending} 个未开始{detail.status === 'cancelled' ? ' · 批次已取消，未开始任务保留展示' : ''}</span>
          <span>{run.dataUpdatedAt ? `更新于 ${new Date(run.dataUpdatedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : '等待同步'}</span>
        </div>
      </section>

      <div className="layout">
        <div>
          <Card>
            <CardHead title="流水线" sub="Run 级阶段" />
            <CardBody>
              <div className="pipeline">
                {visibleStages.map((stage, index) => (
                  <div className="pipe" key={stage.key}>
                    <div className={`pipe-node ${stage.state}`}>
                      <div className="pipe-dot">{stage.state === 'done' ? '✓' : index + 1}</div>
                      <div className="pipe-label">{stage.title || stage.key}</div>
                    </div>
                    {index < visibleStages.length - 1 ? <div className={`pipe-line ${stage.state === 'done' ? 'done' : ''}`} /> : null}
                  </div>
                ))}
              </div>
              {detail.run_lifecycle?.progress?.message ? <p className="note live-progress-message" role="status">{detail.run_lifecycle.progress.message}</p> : null}
              <LogScanProgress run={detail} />
            </CardBody>
          </Card>

          <Card>
            <CardHead
              title="任务"
              sub={<span>{allTotal} 个</span>}
            />
            <div className="card-body tight">
              <div className="toolbar" style={{ paddingTop: 0 }}>
                <div className="chips">
                  {[
                    ['all', '全部'],
                    ['pending', '未开始'],
                    ['active', '执行中'],
                    ['changed_delivered', '已改动交付'],
                    ['unchanged_delivered', '无改动交付'],
                    ['failed', '失败'],
                    ['skipped', '已跳过'],
                    ['paused', '已暂停'],
                  ].map(([key, label]) => (
                    <button key={key} className={`chip${category === key ? ' active' : ''}`} onClick={() => setFilter(key)}>
                      {label} <span className="n">{key === 'all' ? allTotal : counts[key] ?? 0}</span>
                    </button>
                  ))}
                </div>
                <form
                  className="search"
                  onSubmit={(event) => {
                    event.preventDefault();
                    setFilter(category, search.trim());
                  }}
                >
                  <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m16 16 4 4" /></svg>
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="搜索承运商、任务"
                    aria-label="搜索任务"
                    style={{ border: 0, outline: 0, background: 'transparent', width: '100%', color: 'inherit' }}
                  />
                </form>
              </div>
            </div>
            {board.isLoading ? (
              <CardBody><Skeleton rows={5} /></CardBody>
            ) : board.error ? (
              <ErrorBox error={board.error} retry={() => void board.refetch()} />
            ) : tasks.length === 0 ? (
              <div className="empty">
                <div className="empty-icon">·</div>
                <h3>没有匹配的任务</h3>
                <p>调整筛选，或等待批次继续执行。</p>
              </div>
            ) : (
              <div className="card-body flush task-table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>任务</th><th>状态</th><th>进度</th><th>当前步骤</th><th>耗时</th><th>最近活动</th><th /></tr>
                  </thead>
                  <tbody>
                    {tasks.map((task) => (
                      <tr
                        key={task.task_id}
                        style={{ cursor: 'pointer' }}
                        onClick={() => navigate(`/runs/${runId}/tasks/${encodeURIComponent(task.task_id)}`)}
                      >
                        <td>
                          <div className="row-title">{task.carrier}</div>
                          <div className="row-sub">{task.task_id}</div>
                        </td>
                        <td><TaskStatusBadge result={task.result} lifecycle={String((task.lifecycle as Record<string, unknown> | undefined)?.status ?? '')} /></td>
                        <td><CompactTaskProgress task={task} stages={board.data?.stages} /></td>
                        <td className="cell-muted">{task.current_step || task.terminal_step || (task.result === 'PENDING' ? '等待执行' : '—')}</td>
                        <td className="cell-mono">{formatDuration(task.duration_ms ?? durationFrom(String((task.lifecycle as Record<string, unknown> | undefined)?.started_at ?? ''), String((task.lifecycle as Record<string, unknown> | undefined)?.finished_at ?? '')))}</td>
                        <td className="cell-muted nowrap">{relativeTime(String((task.lifecycle as Record<string, unknown> | undefined)?.updated_at ?? ''))}</td>
                        <td className="chev">›</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="card-foot">
              <span>
                共 {filteredTotal} 个任务 · 第 {currentPage} / {pages} 页
              </span>
              <div className="row">
                <button className="btn sm" disabled={currentPage <= 1 || board.isPlaceholderData} onClick={() => setPage(currentPage - 1)}>上一页</button>
                <button className="btn sm" disabled={currentPage >= pages || board.isPlaceholderData} onClick={() => setPage(currentPage + 1)}>下一页</button>
              </div>
            </div>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="批次信息" actions={<RunStatusBadge status={detail.status} />} />
            <CardBody>
              <dl className="kv">
                <dt>批次</dt><dd className="mono">{runId}</dd>
                <dt>开始</dt><dd>{formatBatchTime(detail.started_at)}</dd>
                <dt>任务</dt><dd>{detail.task_count}</dd>
                <dt>审查方式</dt><dd>AI 自动审查</dd>
                <dt>操作人</dt><dd>{detail.operator || 'kirby'}</dd>
              </dl>
              <div className="divider" />
              <div className="row between" style={{ marginBottom: 6 }}>
                <span className="cell-muted">进度</span>
                <span className="mono" style={{ fontSize: 11 }}>{progress.done} / {progress.total}</span>
              </div>
              <ProgressBar pct={progress.pct} tone={attentionCount(detail) ? 'warn' : undefined} />
            </CardBody>
          </Card>

          <Card>
            <CardHead title="下一步" />
            <CardBody>
              <p className="note">{detail.run_lifecycle?.next_action || detail.run_report?.next_action || '等待批次执行完成。'}</p>
            </CardBody>
          </Card>

          <Card>
            <CardHead title="恢复点" actions={<span className="card-sub">{detail.checkpoint?.resumable ? '可恢复' : '不可恢复'}</span>} />
            <CardBody>
              <dl className="kv">
                <dt>节点</dt><dd>{detail.checkpoint?.node || '—'}</dd>
                <dt>创建</dt><dd>{detail.checkpoint?.created_at ? formatBatchTime(detail.checkpoint.created_at) : '—'}</dd>
                <dt>后续</dt><dd className="mono">{detail.checkpoint?.next_nodes?.join(', ') || '—'}</dd>
              </dl>
            </CardBody>
          </Card>
        </aside>
      </div>
    </>
  );
}

function runTone(run: RunDetail) {
  if (run.status === 'failed') return 'danger' as const;
  if (run.status === 'partial_failed' || attentionCount(run) > 0) return 'warn' as const;
  if (run.status === 'succeeded') return 'ok' as const;
  return 'info' as const;
}

function runVerdictTitle(run: RunDetail) {
  if (run.status === 'failed') return '批次执行失败';
  if (run.status === 'partial_failed') return '批次结束，但有任务需要处理';
  if (run.status === 'succeeded') return '批次已完成';
  if (run.status === 'paused') return '批次已暂停';
  if (run.status === 'cancelled') return '批次已取消';
  return `批次 ${shortId(run.run_id, 8)} 正在执行`;
}

function buildStageStatus(steps: StepRecord[], stages: WorkflowStage[]) {
  return stages.map((stage) => {
    const members = steps.filter((step) => stage.members.includes(step.name));
    const failed = members.some((step) => step.status === 'failed');
    const running = members.some((step) => step.status === 'running' || step.status === 'waiting');
    const done = stage.members.every((name) => members.some((step) => step.name === name && ['succeeded', 'skipped'].includes(step.status)));
    return {
      ...stage,
      state: failed ? 'fail' : running ? 'active' : done ? 'done' : '',
    };
  });
}
