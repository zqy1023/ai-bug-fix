import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatDuration, formatTime, formatTokens } from '../lib/format';
import { Card, CardBody, CardHead, ErrorBox, JsonBlock, Skeleton } from '../components/ui';
import { TaskAuditTabs, TaskHeader } from '../components/TaskHeader';
import type { AgentEvent } from '../lib/types';

export function TaskAgentPage() {
  const { runId = '', taskId = '' } = useParams();
  const [params] = useSearchParams();
  const agent = params.get('agent');
  const [selected, setSelected] = useState('');
  useEffect(() => setSelected(''), [runId, taskId, agent]);
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
    if (!selected && calls.data?.items.length) {
      const match = agent ? calls.data.items.find(item => item.agent_role === agent) : undefined;
      setSelected((match || calls.data.items[0]).model_call_id);
    }
  }, [calls.data, selected, agent]);

  const call = useQuery({
    queryKey: ['agent-call', runId, taskId, selected],
    queryFn: () => api.agentCall(runId, taskId, selected),
    enabled: Boolean(runId && taskId && selected),
  });

  if (detail.isLoading && !detail.data) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detail.error || !detail.data) return <Card><ErrorBox error={detail.error ?? new Error('任务不存在')} /></Card>;

  const items = calls.data?.items ?? [];
  const current = items.find((item) => item.model_call_id === selected);
  const events = call.data?.events ?? [];

  return (
    <>
      <TaskHeader detail={detail.data} active="audit" />
      <TaskAuditTabs active="agent" />

      <div style={{ display: 'grid', gridTemplateColumns: '300px minmax(0,1fr)', gap: 14, marginTop: 16, alignItems: 'start' }}>
        <Card>
          <CardHead title="调用记录" sub={`${items.length} 次`} />
          {calls.isLoading ? (
            <CardBody><Skeleton rows={4} /></CardBody>
          ) : items.length === 0 ? (
            <CardBody><p className="note">没有模型调用记录。</p></CardBody>
          ) : (
            <div className="card-body flush">
              {items.map((item) => (
                <button
                  key={item.model_call_id}
                  onClick={() => setSelected(item.model_call_id)}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    border: 0,
                    borderBottom: '1px solid var(--line)',
                    borderLeft: item.model_call_id === selected ? '2px solid var(--primary)' : '2px solid transparent',
                    background: item.model_call_id === selected ? '#f8fafc' : '#fff',
                    padding: '10px 12px',
                    cursor: 'pointer',
                  }}
                >
                  <div className="row between">
                    <span className="row-title" style={{ fontSize: 12.5 }}>{item.agent_role}</span>
                    <span className="cell-mono">{formatTokens(item.total_tokens)}</span>
                  </div>
                  <div className="row-sub">{formatTime(item.started_at)} · {item.status}</div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <div>
          <Card>
            <CardHead
              title="Agent 对话"
              sub={current ? `${current.agent_role} · ${current.model}` : '选择一次调用'}
              actions={
                call.data?.prompt_snapshot_available ? (
                  <Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit/prompt`}>Prompt 快照 →</Link>
                ) : undefined
              }
            />
            <CardBody>
              {!selected ? (
                <p className="note">选择左侧的一次模型调用查看对话。</p>
              ) : call.isLoading ? (
                <Skeleton rows={6} />
              ) : call.error ? (
                <ErrorBox error={call.error} retry={() => void call.refetch()} />
              ) : events.length === 0 ? (
                <p className="note">这次调用没有事件记录。</p>
              ) : (
                <Conversation events={events} />
              )}
            </CardBody>
          </Card>

          {current ? (
            <div className="two-col" style={{ marginTop: 14 }}>
              <Card>
                <CardHead title="Token" />
                <CardBody>
                  <dl className="kv">
                    <dt>输入</dt><dd>{formatTokens(current.input_tokens)}</dd>
                    <dt>输出</dt><dd>{formatTokens(current.output_tokens)}</dd>
                    <dt>缓存</dt><dd>{formatTokens(current.cache_tokens)}</dd>
                    <dt>合计</dt><dd>{formatTokens(current.total_tokens)}</dd>
                    <dt>耗时</dt><dd>{formatDuration(current.duration_ms)}</dd>
                  </dl>
                </CardBody>
              </Card>
              <Card>
                <CardHead title="工具" />
                <CardBody>
                  <dl className="kv">
                    <dt>调用</dt><dd>{current.tool_call_count ?? '—'}</dd>
                    <dt>允许</dt><dd>{current.tool_allowed ?? '—'}</dd>
                    <dt>拒绝</dt><dd>{current.tool_denied ?? '—'}</dd>
                    <dt>完成</dt><dd>{current.tool_completed ?? '—'}</dd>
                    <dt>事件</dt><dd>{current.event_count ?? events.length}</dd>
                  </dl>
                </CardBody>
              </Card>
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}

function Conversation({ events }: { events: AgentEvent[] }) {
  const toolNames = useMemo(() => {
    const map = new Map<string, string>();
    events.forEach((event) => {
      if (event.type === 'tool_use' && event.tool_use_id) map.set(event.tool_use_id, event.tool ?? 'tool');
    });
    return map;
  }, [events]);

  return (
    <div className="chat">
      {events.map((event, index) => {
        if (event.type === 'thinking') {
          return (
            <div className="msg" key={index}>
              <div className="who">思</div>
              <div>
                <div className="meta"><span>思考</span>{event.redacted ? <span>已脱敏</span> : null}</div>
                <div className="bubble think">{event.text || '（redacted thinking）'}</div>
              </div>
            </div>
          );
        }
        if (event.type === 'assistant') {
          return (
            <div className="msg" key={index}>
              <div className="who">AI</div>
              <div>
                <div className="meta"><span>assistant</span>{event.final ? <span>最终输出</span> : null}</div>
                <div className="bubble">{event.text || '—'}</div>
              </div>
            </div>
          );
        }
        if (event.type === 'tool_use') {
          return (
            <div className="msg" key={index}>
              <div className="who tool">T</div>
              <div>
                <div className="meta"><span>tool_use</span><span className="mono">{event.tool}</span></div>
                <div className="tool-card">
                  <div className="tool-head">
                    <span className="tool-name">{event.tool}</span>
                    <span className="tool-args">{summarize(event.input)}</span>
                  </div>
                  <pre>{JSON.stringify(event.input ?? {}, null, 2)}</pre>
                </div>
              </div>
            </div>
          );
        }
        if (event.type === 'tool_result') {
          const name = event.tool_use_id ? toolNames.get(event.tool_use_id) : undefined;
          return (
            <div className="msg" key={index}>
              <div className="who tool">R</div>
              <div>
                <div className="meta"><span>tool_result</span>{name ? <span className="mono">{name}</span> : null}{event.is_error ? <span className="badge danger">错误</span> : null}</div>
                <div className="tool-card">
                  <pre>{event.text || '—'}</pre>
                </div>
              </div>
            </div>
          );
        }
        return (
          <div className="msg" key={index}>
            <div className="who tool">·</div>
            <div><JsonBlock value={event} maxHeight={180} /></div>
          </div>
        );
      })}
    </div>
  );
}

function summarize(input?: Record<string, unknown>): string {
  if (!input) return '';
  const text = JSON.stringify(input);
  return text.length > 100 ? `${text.slice(0, 100)}…` : text;
}
