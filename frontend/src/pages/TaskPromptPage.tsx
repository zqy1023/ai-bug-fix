import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatTime, shortId } from '../lib/format';
import { Card, CardBody, CardHead, CopyButton, ErrorBox, Skeleton, useToast } from '../components/ui';
import { TaskAuditTabs, TaskHeader } from '../components/TaskHeader';
import type { PromptSnapshot } from '../lib/types';

export function TaskPromptPage() {
  const { runId = '', taskId = '' } = useParams();
  const [selected, setSelected] = useState('');
  const { push } = useToast();
  const detail = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
  });
  const calls = useQuery({
    queryKey: ['model-calls', runId, taskId],
    queryFn: () => api.modelCalls(runId, taskId),
    enabled: Boolean(runId && taskId),
  });

  useEffect(() => {
    if (!selected && calls.data?.items.length) setSelected(calls.data.items[0].model_call_id);
  }, [calls.data, selected]);

  const call = useQuery({
    queryKey: ['agent-call', runId, taskId, selected],
    queryFn: () => api.agentCall(runId, taskId, selected),
    enabled: Boolean(runId && taskId && selected),
  });

  const snapshot = useMemo<PromptSnapshot | null>(() => {
    const value = call.data?.prompt_snapshot;
    if (!value || !('system_prompt_common' in value)) return null;
    return value as PromptSnapshot;
  }, [call.data]);

  if (detail.isLoading && !detail.data) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detail.error || !detail.data) return <Card><ErrorBox error={detail.error ?? new Error('任务不存在')} /></Card>;

  const items = calls.data?.items ?? [];
  const download = () => {
    if (!snapshot) return;
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `prompt-${snapshot.model_call_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    push('已下载 Prompt 快照');
  };

  return (
    <>
      <TaskHeader detail={detail.data} active="audit" />
      <TaskAuditTabs active="prompt" />

      <div className="layout">
        <div>
          <Card>
            <CardHead
              title="Prompt 快照"
              sub={snapshot ? `${snapshot.agent_role} · ${snapshot.model}` : '选择一次模型调用'}
              actions={
                snapshot ? (
                  <div className="row">
                    <CopyButton text={JSON.stringify(snapshot, null, 2)} label="复制全部" />
                    <button className="btn sm" onClick={download}>下载</button>
                  </div>
                ) : undefined
              }
            />
            <CardBody>
              {call.isLoading ? <Skeleton rows={8} /> : null}
              {call.error ? <ErrorBox error={call.error} retry={() => void call.refetch()} /> : null}
              {!call.isLoading && !snapshot ? <p className="note">这次调用没有保存 Prompt 快照。</p> : null}
              {snapshot ? (
                <div className="stack-12">
                  <PromptBlock title="system_prompt_common" text={snapshot.system_prompt_common} chars={snapshot.chars.system_prompt_common} />
                  <PromptBlock title="system_prompt_role" text={snapshot.system_prompt_role} chars={snapshot.chars.system_prompt_role} />
                  <PromptBlock title="task_prompt" text={snapshot.task_prompt} chars={snapshot.chars.task_prompt} />
                  <PromptBlock title="prompt_context" text={snapshot.prompt_context} chars={snapshot.chars.prompt_context} />
                  {snapshot.full_task_context ? (
                    <PromptBlock title="full_task_context" text={snapshot.full_task_context} chars={snapshot.chars.full_task_context} />
                  ) : null}
                </div>
              ) : null}
            </CardBody>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="调用" />
            <CardBody>
              {calls.isLoading ? <Skeleton rows={3} /> : null}
              {items.map((item) => (
                <button
                  key={item.model_call_id}
                  onClick={() => setSelected(item.model_call_id)}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left', border: 0,
                    borderLeft: item.model_call_id === selected ? '2px solid var(--primary)' : '2px solid transparent',
                    background: item.model_call_id === selected ? '#f8fafc' : '#fff',
                    borderBottom: '1px solid var(--line)', padding: '9px 10px', cursor: 'pointer',
                  }}
                >
                  <div className="row-title" style={{ fontSize: 12 }}>{item.agent_role}</div>
                  <div className="row-sub">{formatTime(item.started_at)}</div>
                </button>
              ))}
            </CardBody>
          </Card>

          {snapshot ? (
            <Card>
              <CardHead title="快照信息" />
              <CardBody>
                <dl className="kv">
                  <dt>调用</dt><dd className="mono">{shortId(snapshot.model_call_id, 12)}</dd>
                  <dt>模型</dt><dd>{snapshot.model}</dd>
                  <dt>版本</dt><dd>{snapshot.prompt_version}</dd>
                  <dt>hash</dt><dd className="mono">{shortId(snapshot.prompt_hash, 12)}</dd>
                  <dt>字符</dt><dd>{Object.values(snapshot.chars).reduce((sum, value) => sum + value, 0).toLocaleString()}</dd>
                  <dt>截断</dt><dd>{snapshot.truncated ? '是' : '否'}</dd>
                  <dt>脱敏</dt><dd>{snapshot.redacted ? '是' : '否'}</dd>
                </dl>
              </CardBody>
            </Card>
          ) : null}
        </aside>
      </div>
    </>
  );
}

function PromptBlock({ title, text, chars }: { title: string; text: string; chars?: number }) {
  return (
    <div className="prompt-block">
      <div className="pb-head">
        <span className="mono" style={{ fontSize: 11 }}>{title}</span>
        <span className="cell-muted" style={{ marginLeft: 'auto' }}>{(chars ?? text.length).toLocaleString()} 字符</span>
        <CopyButton text={text} label="复制" />
      </div>
      <div className="pb-body">
        <pre>{text || '—'}</pre>
      </div>
    </div>
  );
}
