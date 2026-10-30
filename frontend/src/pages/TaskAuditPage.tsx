import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { durationFrom, formatDuration, formatTokens } from '../lib/format';
import {
  Button,
  Card,
  CardBody,
  CardHead,
  Drawer,
  ErrorBox,
  JsonBlock,
  Skeleton,
  useToast,
} from '../components/ui';
import { TaskAuditTabs, TaskHeader } from '../components/TaskHeader';
import type { StepRecord } from '../lib/types';
import { stepPresentation } from '../lib/stepPresentation';

export function TaskAuditPage() {
  const { runId = '', taskId = '' } = useParams();
  const [selected, setSelected] = useState<string>('');
  const queryClient = useQueryClient();
  const { push } = useToast();
  const detailQuery = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
  });
  const detail = detailQuery.data;
  const stepQuery = useQuery({
    queryKey: ['step', runId, taskId, selected],
    queryFn: () => api.step(runId, taskId, selected),
    enabled: Boolean(runId && taskId && selected),
  });
  const modelCalls = useQuery({
    queryKey: ['model-calls', runId, taskId],
    queryFn: () => api.modelCalls(runId, taskId),
    enabled: Boolean(runId && taskId),
  });

  const action = useMutation({
    mutationFn: (kind: 'rerun' | 'continue') => {
      const step = stepQuery.data?.step;
      if (!step) throw new Error('步骤未加载');
      return api.runStep(runId, taskId, step.name, Boolean(stepQuery.data?.actions?.rerun.requires_confirmation), kind === 'continue');
    },
    onSuccess: () => {
      push('步骤已提交');
      setSelected('');
      void queryClient.invalidateQueries({ queryKey: ['task', runId, taskId] });
      void queryClient.invalidateQueries({ queryKey: ['run', runId] });
    },
    onError: (error) => push('操作失败', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  if (detailQuery.isLoading && !detail) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detailQuery.error || !detail) return <Card><ErrorBox error={detailQuery.error ?? new Error('任务不存在')} /></Card>;

  const calls = modelCalls.data?.items ?? [];
  const totalTokens = calls.reduce((sum, call) => sum + (call.total_tokens ?? 0), 0);
  const duration = detail.task.duration_ms ?? detail.task.report.elapsed_ms
    ?? durationFrom(detail.task.lifecycle?.started_at, detail.task.lifecycle?.finished_at);
  const terminalName = detail.task.report.terminal_step || detail.task.lifecycle?.terminal_step || '';
  const terminalTitle = detail.task.steps.find((step) => step.name === terminalName)?.title || terminalName || '—';

  return (
    <>
      <TaskHeader detail={detail} active="audit" />
      <TaskAuditTabs active="steps" />

      <div className="layout">
        <div>
          <Card>
            <CardHead title="执行时间线" sub={`${detail.task.steps.length} 步`} />
            <CardBody>
              <div className="timeline">
                {detail.task.steps.map((step) => (
                  <button
                    key={step.name}
                    className={`tl-item ${stepTone(step)}`}
                    style={{ border: 0, background: 'transparent', textAlign: 'left', width: '100%', cursor: 'pointer' }}
                    onClick={() => setSelected(step.name)}
                  >
                    <div className="tl-head">
                      <span className="tl-title">{step.title || step.name}</span>
                      <span className="tag">{stepPresentation(step).label}</span>
                      <span className="tl-time">{formatDuration(step.duration_ms)}</span>
                    </div>
                    <div className="tl-desc">{step.summary || step.error?.message || '—'}</div>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="执行摘要" />
            <CardBody>
              <div className="metric-inline" style={{ flexWrap: 'wrap', gap: 14 }}>
                <span>步骤 <b>{detail.task.steps.length}</b></span>
                <span>耗时 <b>{formatDuration(duration)}</b></span>
                <span>Tokens <b>{formatTokens(totalTokens)}</b></span>
              </div>
              <div className="divider" />
              <p className="note">停在「{terminalTitle}」。</p>
            </CardBody>
          </Card>

          <Card>
            <CardHead title="Agent 调用" sub={`${calls.length} 次`} actions={<Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit/agent`}>查看对话</Link>} />
            <CardBody>
              {modelCalls.isLoading ? <Skeleton rows={3} /> : null}
              {calls.length === 0 && !modelCalls.isLoading ? <p className="note">没有模型调用记录。</p> : null}
              {calls.map((call) => (
                <div className="row between" key={call.model_call_id} style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                  <span style={{ fontSize: 12 }}>{call.agent_role}</span>
                  <span className="cell-mono">{formatTokens(call.total_tokens)}</span>
                </div>
              ))}
            </CardBody>
          </Card>
        </aside>
      </div>

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected('')}
        title={stepQuery.data?.step.title || stepQuery.data?.step.name || '步骤'}
        desc={stepQuery.data?.step.summary || stepQuery.data?.step.status}
        footer={
          stepQuery.data ? (
            <>
              <span className="cell-muted">写操作会要求确认</span>
              <div className="row">
                {stepQuery.data.actions?.rerun.enabled ? (
                  <Button size="sm" disabled={action.isPending} onClick={() => action.mutate('rerun')}>重跑</Button>
                ) : null}
                {stepQuery.data.actions?.continue_after.enabled ? (
                  <Button size="sm" variant="primary" disabled={action.isPending} onClick={() => action.mutate('continue')}>从这里继续</Button>
                ) : null}
              </div>
            </>
          ) : undefined
        }
      >
        {stepQuery.isLoading ? <Skeleton rows={6} /> : null}
        {stepQuery.error ? <ErrorBox error={stepQuery.error} retry={() => void stepQuery.refetch()} /> : null}
        {stepQuery.data ? (
          <div className="stack-12">
            <Card>
              <CardHead title="状态" />
              <CardBody>
                <dl className="kv">
                  <dt>名称</dt><dd className="mono">{stepQuery.data.step.name}</dd>
                  <dt>处理结果</dt><dd>{stepPresentation(stepQuery.data.step).label}</dd>
                  <dt>节点执行记录</dt><dd>{stepQuery.data.step.status}</dd>
                  <dt>尝试</dt><dd>{stepQuery.data.context.attempt}</dd>
                  <dt>耗时</dt><dd>{formatDuration(stepQuery.data.step.duration_ms)}</dd>
                </dl>
              </CardBody>
            </Card>
            {stepQuery.data.step.input ? <Card><CardHead title="输入" /><CardBody><JsonBlock value={stepQuery.data.step.input} maxHeight={240} /></CardBody></Card> : null}
            {stepQuery.data.step.output ? <Card><CardHead title="输出" /><CardBody><JsonBlock value={stepQuery.data.step.output} maxHeight={320} /></CardBody></Card> : null}
            {stepQuery.data.step.error ? <Card><CardHead title="错误" /><CardBody><p className="note">{stepQuery.data.step.error.message}</p></CardBody></Card> : null}
            {stepQuery.data.artifacts.length > 0 ? (
              <Card>
                <CardHead title="步骤产物" />
                <CardBody>
                  {stepQuery.data.artifacts.map((artifact) => (
                    <div className="row between" key={artifact.path} style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                      <span className="mono" style={{ fontSize: 11 }}>{artifact.path}</span>
                      <span className="tag">{artifact.media_type?.split('/').pop()?.toUpperCase() || 'JSON'}</span>
                    </div>
                  ))}
                </CardBody>
              </Card>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </>
  );
}

function stepTone(step: StepRecord) {
  return stepPresentation(step).tone;
}
