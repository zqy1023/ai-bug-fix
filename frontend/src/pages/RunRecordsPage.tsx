import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatBatchTime, formatDuration, formatTime, shortId } from '../lib/format';
import {
  Card,
  CardBody,
  CardHead,
  Drawer,
  ErrorBox,
  JsonBlock,
  PageHeader,
  Skeleton,
  Tabs,
} from '../components/ui';
import type { StepRecord } from '../lib/types';

export function RunRecordsPage() {
  const { runId = '' } = useParams();
  const [selected, setSelected] = useState<StepRecord | null>(null);
  const run = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), enabled: Boolean(runId) });
  const audit = useQuery({ queryKey: ['control-audit', runId], queryFn: () => api.controlAudit(runId), enabled: Boolean(runId) });
  const checkpoints = useQuery({ queryKey: ['checkpoints', runId], queryFn: () => api.checkpoints(runId), enabled: Boolean(runId) });

  const detail = run.data;

  if (run.isLoading && !detail) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (run.error || !detail) return <Card><ErrorBox error={run.error ?? new Error('批次不存在')} /></Card>;

  return (
    <>
      <PageHeader
        eyebrow={`Run / ${shortId(runId, 8)}`}
        title={<>批次 <span className="mono">{shortId(runId, 8)}</span></>}
        meta={<>{detail.task_count} 个任务<span className="dot">·</span>{formatBatchTime(detail.started_at)}</>}
        actions={<Link className="btn" to={`/runs/${runId}`}>返回任务</Link>}
      />
      <Tabs
        active="records"
        items={[
          { key: 'tasks', label: '任务', count: detail.task_count, href: `/runs/${runId}` },
          { key: 'records', label: '记录', href: `/runs/${runId}/records` },
          { key: 'artifacts', label: '产物', count: detail.artifacts.length, href: `/runs/${runId}/artifacts` },
        ]}
      />

      <div className="layout">
        <div>
          <Card>
            <CardHead title="执行阶段" sub="Run 级步骤" />
            <CardBody>
              <div className="timeline">
                {detail.steps.map((step) => (
                  <button
                    key={step.name}
                    className={`tl-item ${stepTone(step.status)}`}
                    style={{ border: 0, background: 'transparent', textAlign: 'left', width: '100%', cursor: 'pointer' }}
                    onClick={() => setSelected(step)}
                  >
                    <div className="tl-head">
                      <span className="tl-title">{step.title || step.name}</span>
                      <span className="tl-time">{formatDuration(step.duration_ms)}</span>
                    </div>
                    <div className="tl-desc">{step.summary || step.error?.message || step.status}</div>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHead title="控制审计" sub={`${audit.data?.total ?? 0} 条`} />
            {audit.isLoading ? (
              <CardBody><Skeleton rows={3} /></CardBody>
            ) : (
              <div className="card-body flush">
                <table className="table">
                  <thead><tr><th>动作</th><th>对象</th><th>操作人</th><th>原因</th><th>时间</th></tr></thead>
                  <tbody>
                    {(audit.data?.items ?? []).map((item) => (
                      <tr key={item.id}>
                        <td className="mono">{item.action}</td>
                        <td className="cell-mono">{item.task_id ? shortId(item.task_id.split('__')[0], 18) : '—'}</td>
                        <td>{item.actor}</td>
                        <td className="cell-muted">{item.reason}</td>
                        <td className="cell-muted nowrap">{formatTime(item.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(audit.data?.items.length ?? 0) === 0 ? <div className="empty"><h3>没有控制操作</h3><p>暂停、恢复、重试、取消会记录在这里。</p></div> : null}
              </div>
            )}
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="运行信息" />
            <CardBody>
              <dl className="kv">
                <dt>批次</dt><dd className="mono">{runId}</dd>
                <dt>开始</dt><dd>{formatBatchTime(detail.started_at)}</dd>
                <dt>结束</dt><dd>{detail.finished_at ? formatBatchTime(detail.finished_at) : '—'}</dd>
                <dt>尝试</dt><dd>{detail.run_lifecycle?.attempt ?? 1}</dd>
                <dt>心跳</dt><dd>{detail.run_lifecycle?.heartbeat_at ? formatBatchTime(detail.run_lifecycle.heartbeat_at) : '—'}</dd>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHead title="恢复点" sub={`${checkpoints.data?.total ?? 0} 个`} />
            <CardBody>
              {checkpoints.isLoading ? <Skeleton rows={3} /> : null}
              {(checkpoints.data?.items ?? []).slice(0, 5).map((item, index) => (
                <div key={`${item.checkpoint_id}-${index}`} className="row between" style={{ padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
                  <div>
                    <div className="mono" style={{ fontSize: 11 }}>{item.node || 'checkpoint'}</div>
                    <div className="row-sub">{item.created_at ? formatBatchTime(item.created_at) : '—'}</div>
                  </div>
                  <span className={`tag${item.next_nodes.length ? ' blue' : ''}`}>{item.next_nodes.length ? '可恢复' : '终态'}</span>
                </div>
              ))}
              {(checkpoints.data?.items.length ?? 0) === 0 ? <p className="note">暂无恢复点。</p> : null}
            </CardBody>
          </Card>
        </aside>
      </div>

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.title || selected?.name || '步骤'}
        desc={selected?.summary || selected?.status}
      >
        {selected ? (
          <div className="stack-12">
            <Card>
              <CardHead title="状态" />
              <CardBody>
                <dl className="kv">
                  <dt>名称</dt><dd className="mono">{selected.name}</dd>
                  <dt>状态</dt><dd>{selected.status}</dd>
                  <dt>尝试</dt><dd>{selected.attempt ?? 1}</dd>
                  <dt>耗时</dt><dd>{formatDuration(selected.duration_ms)}</dd>
                </dl>
              </CardBody>
            </Card>
            {selected.input ? <Card><CardHead title="输入" /><CardBody><JsonBlock value={selected.input} maxHeight={220} /></CardBody></Card> : null}
            {selected.output ? <Card><CardHead title="输出" /><CardBody><JsonBlock value={selected.output} maxHeight={320} /></CardBody></Card> : null}
            {selected.error ? <Card><CardHead title="错误" /><CardBody><p className="note">{selected.error.message}</p></CardBody></Card> : null}
          </div>
        ) : null}
      </Drawer>
    </>
  );
}

function stepTone(status: StepRecord['status']) {
  if (status === 'failed') return 'bad';
  if (status === 'running' || status === 'waiting') return 'active';
  if (status === 'skipped') return 'warn';
  return 'ok';
}
