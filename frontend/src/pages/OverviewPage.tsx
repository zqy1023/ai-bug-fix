import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatBatchTime, relativeTime, shortId } from '../lib/format';
import { attentionCount, isRunActive, needsAttention, runProgress } from '../lib/status';
import { useRunEvents } from '../lib/hooks';
import {
  Card,
  CardBody,
  CardHead,
  Empty,
  ErrorBox,
  Kpi,
  PageHeader,
  ProgressBar,
  RunStatusBadge,
  Skeleton,
  TaskStatusBadge,
  Verdict,
} from '../components/ui';

function needsTasks(tasks: Array<{ result: string; carrier: string; task_id: string; terminal_reason: string; runId: string; updated?: string }>) {
  return tasks.filter((task) => needsAttention({ result: task.result }));
}

export function OverviewPage() {
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15_000 });
  const runs = useQuery({
    queryKey: ['runs', 'overview'],
    queryFn: () => api.runs({ page: 1, page_size: 8 }),
    refetchInterval: 20_000,
  });

  const activeRun = useMemo(() => {
    const all = [...(dashboard.data?.recent_runs ?? []), ...(runs.data?.items ?? [])];
    return all.find((run) => isRunActive(run.status));
  }, [dashboard.data, runs.data]);
  useRunEvents(activeRun?.run_id);

  const attentionRunId =
    runs.data?.attention_run_id ||
    [...(dashboard.data?.recent_runs ?? [])].find((run) => attentionCount(run) > 0)?.run_id;

  const attentionBoard = useQuery({
    queryKey: ['board', attentionRunId, 'overview'],
    queryFn: () => api.board(attentionRunId as string, { page_size: 100 }),
    enabled: Boolean(attentionRunId),
  });

  const activeDetail = useQuery({
    queryKey: ['run', activeRun?.run_id],
    queryFn: () => api.run(activeRun?.run_id as string),
    enabled: Boolean(activeRun?.run_id),
  });

  const needs = useMemo(() => {
    const rows = (attentionBoard.data?.tasks ?? []).map((task) => ({
      result: task.result,
      carrier: task.carrier,
      task_id: task.task_id,
      terminal_reason: task.terminal_reason || task.next_action,
      runId: attentionRunId ?? '',
      updated: String((task.lifecycle as Record<string, unknown> | undefined)?.updated_at ?? ''),
    }));
    return needsTasks(rows);
  }, [attentionBoard.data, attentionRunId]);

  const attentionTotal =
    runs.data?.counts?.attention ??
    [...(dashboard.data?.recent_runs ?? [])].reduce((sum, run) => sum + attentionCount(run), 0);

  const todayRuns = dashboard.data?.recent_runs?.length ?? 0;
  const succeeded = dashboard.data?.succeeded_24h ?? 0;
  const rate = dashboard.data?.success_rate_7d ?? 0;
  const error = dashboard.error || runs.error;

  return (
    <>
      <PageHeader
        eyebrow="Overview / 概览"
        title="概览"
        meta={`今天 ${todayRuns} 轮，${succeeded} 个任务已交付，${attentionTotal} 个需要你处理。`}
        actions={
          <>
            <Link className="btn" to="/runs">查看全部批次</Link>
            <Link className="btn primary" to="/runs?new=1">新建批次</Link>
          </>
        }
      />

      {error ? (
        <Card><ErrorBox error={error} retry={() => { void dashboard.refetch(); void runs.refetch(); }} /></Card>
      ) : (
        <>
          {activeRun ? (
            <Verdict
              tone="info"
              icon="●"
              title={`批次 ${shortId(activeRun.run_id, 8)} 正在执行`}
              desc={
                <>
                  {runProgress(activeRun).done} / {activeRun.task_count} 完成
                  <span className="dot">·</span>
                  {attentionCount(activeRun)} 个需处理
                  <span className="dot">·</span>
                  当前 {activeDetail.data?.run_lifecycle?.current_step || '执行中'}
                </>
              }
              actions={<Link className="link" to={`/runs/${activeRun.run_id}`}>查看批次 →</Link>}
            />
          ) : attentionTotal > 0 ? (
            <Verdict
              tone="warn"
              title={`${attentionTotal} 个任务需要你处理`}
              desc="先看等待最久的任务，处理完再开新批次。"
              actions={<Link className="link" to={`/runs/${attentionRunId}`}>先看最紧急的 →</Link>}
            />
          ) : (
            <Verdict
              tone="ok"
              icon="✓"
              title="当前没有需要处理的任务"
              desc="可以创建一个新批次，或查看历史交付。"
              actions={<Link className="link" to="/runs">查看批次 →</Link>}
            />
          )}

          <div className="kpis">
            <Kpi value={dashboard.data?.active_runs ?? 0} label="活跃批次" />
            <Kpi value={succeeded} label="24h 已交付" tone="ok" />
            <Kpi value={<>{Math.round(rate * 100)}<small>%</small></>} label="7 日成功率" />
          </div>

          <div className="layout">
            <div>
              <Card>
                <CardHead
                  title={`需要处理 · ${needs.length}`}
                  sub={attentionTotal > needs.length ? `全部 ${attentionTotal}` : undefined}
                  actions={<Link className="link" to="/runs">查看全部</Link>}
                />
                {attentionBoard.isLoading ? (
                  <CardBody><Skeleton rows={3} /></CardBody>
                ) : needs.length === 0 ? (
                  <Empty title="没有失败任务" desc="执行失败的任务会出现在这里，交付与跳过结果可在批次查看。" />
                ) : (
                  <div className="card-body flush">
                    <table className="table">
                      <tbody>
                        {needs.slice(0, 4).map((task) => (
                          <tr key={task.task_id}>
                            <td>
                              <div className="row-title">
                                {task.carrier}
                                <TaskStatusBadge result={task.result} />
                              </div>
                              <div className="row-sub">{shortId(task.runId)} · {task.task_id.split('__')[1] || 'task'}</div>
                            </td>
                            <td className="cell-muted">{task.terminal_reason || '等待处理'}</td>
                            <td className="cell-muted nowrap">{task.updated ? relativeTime(task.updated) : '—'}</td>
                            <td className="cell-right"><Link className="link" to={`/runs/${task.runId}/tasks/${encodeURIComponent(task.task_id)}`}>处理 →</Link></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card>
                <CardHead title="最近批次" sub="最近 7 天" actions={<Link className="link" to="/runs">查看全部</Link>} />
                {runs.isLoading ? (
                  <CardBody><Skeleton rows={3} /></CardBody>
                ) : (runs.data?.items.length ?? 0) === 0 ? (
                  <Empty title="还没有批次" desc="新建一个批次开始修复。" action={<Link className="btn primary" to="/runs?new=1">新建批次</Link>} />
                ) : (
                  <div className="card-body flush">
                    <table className="table">
                      <thead>
                        <tr><th>批次</th><th>结论</th><th>进度</th><th>需处理</th><th>时间</th></tr>
                      </thead>
                      <tbody>
                        {(runs.data?.items ?? []).slice(0, 4).map((run) => (
                          <tr key={run.run_id}>
                            <td>
                              <Link to={`/runs/${run.run_id}`} className="row-title">{shortId(run.run_id, 8)}</Link>
                              <div className="row-sub">{run.task_count} 个任务</div>
                            </td>
                            <td><RunStatusBadge status={run.status} /></td>
                            <td>
                              <div className="bar-row">
                                <ProgressBar pct={runProgress(run).pct} tone={attentionCount(run) ? 'warn' : undefined} />
                                <span className="txt">{runProgress(run).done} / {run.task_count}</span>
                              </div>
                            </td>
                            <td className={attentionCount(run) ? 'cell-mono' : 'cell-muted'}>{attentionCount(run) || '—'}</td>
                            <td className="cell-muted nowrap">{formatBatchTime(run.started_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card>
                <CardHead title="近 7 日" actions={<div className="legend"><span><i style={{ background: 'var(--ok)' }} />已交付</span><span><i style={{ background: '#fecaca' }} />失败</span></div>} />
                <CardBody>
                  <TrendChart data={dashboard.data?.daily_trend ?? []} />
                </CardBody>
              </Card>
            </div>

            <aside className="side">
              <Card>
                <CardHead title="当前批次" actions={activeRun ? <RunStatusBadge status={activeRun.status} /> : undefined} />
                <CardBody>
                  {activeRun ? (
                    <>
                      <div className="row between" style={{ marginBottom: 6 }}>
                        <span className="mono" style={{ fontSize: 11.5, color: 'var(--muted)' }}>{shortId(activeRun.run_id, 10)}</span>
                        <span className="cell-muted">{runProgress(activeRun).done} / {activeRun.task_count}</span>
                      </div>
                      <ProgressBar pct={runProgress(activeRun).pct} />
                      <div className="row between" style={{ marginTop: 6 }}>
                        <span className="cell-muted">{activeRun.succeeded_tasks} 已交付 · {attentionCount(activeRun)} 需处理</span>
                        <span className="cell-muted">{activeRun.task_count} 总数</span>
                      </div>
                      <div className="divider" />
                      <dl className="kv">
                        <dt>当前任务</dt><dd>{activeDetail.data?.run_lifecycle?.current_task_id?.split('__')[0] || '—'}</dd>
                        <dt>当前步骤</dt><dd>{activeDetail.data?.run_lifecycle?.current_step || '—'}</dd>
                        <dt>审查方式</dt><dd>AI 自动审查</dd>
                      </dl>
                    </>
                  ) : (
                    <p className="note">当前没有执行中的批次。</p>
                  )}
                </CardBody>
              </Card>
            </aside>
          </div>
        </>
      )}
    </>
  );
}

function TrendChart({ data }: { data: Array<{ date: string; succeeded: number; failed: number }> }) {
  if (!data.length) return <p className="note">暂无趋势数据。</p>;
  const max = Math.max(...data.map((item) => item.succeeded + item.failed), 1);
  return (
    <div className="chart">
      {data.map((item) => (
        <div className="col" key={item.date}>
          <div className="stack">
            <div className="f" style={{ height: Math.round((item.failed / max) * 36) }} />
            <div className="s" style={{ height: Math.max(3, Math.round((item.succeeded / max) * 36)) }} />
          </div>
          <div className="lbl">{item.date.slice(5)}</div>
        </div>
      ))}
    </div>
  );
}
