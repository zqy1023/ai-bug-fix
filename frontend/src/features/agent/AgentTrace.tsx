import { useEffect, useMemo, useState } from 'react';
import { Divider, Select, Skeleton } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration, formatTokens, shortId } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';
import { PromptViewer } from './PromptViewer';
import { ToolCallTimeline } from './ToolCallTimeline';
import { ModelOutputPanel } from './ModelOutputPanel';

function callStatusLabel(status: string): string {
  return ({
    succeeded: '成功',
    failed: '失败',
    running: '运行中',
    pending: '等待中',
    cancelled: '已取消',
  } as Record<string, string>)[status] || status || '未知';
}

export function AgentTrace({
  runId, taskId, stepName, attempt,
}: { runId: string; taskId: string; stepName: string; attempt?: number }) {
  const role = stepName.replace(/_agent$/, '');
  const callsQuery = useQuery({
    queryKey: queryKeys.modelCalls(runId, taskId),
    queryFn: () => api.modelCalls(runId, taskId),
    refetchInterval: 8000,
  });

  const calls = useMemo(() => {
    const all = (callsQuery.data?.items ?? []).filter((call) => call.agent_role === role);
    // If this specific attempt has no recorded call, do not display calls from a previous attempt as if current.
    return attempt ? all.filter((call) => call.attempt === attempt) : all;
  }, [callsQuery.data?.items, role, attempt]);

  const [selectedId, setSelectedId] = useState('');
  useEffect(() => {
    if (!calls.length) return;
    if (!calls.some((call) => call.model_call_id === selectedId)) {
      setSelectedId(calls[calls.length - 1].model_call_id);
    }
  }, [calls, selectedId]);

  const selected = calls.find((call) => call.model_call_id === selectedId) ?? calls[calls.length - 1];
  const detail = useQuery({
    queryKey: queryKeys.agentCall(runId, taskId, selected?.model_call_id ?? ''),
    queryFn: () => api.agentCall(runId, taskId, selected!.model_call_id),
    enabled: Boolean(selected?.model_call_id),
  });

  if (callsQuery.isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (callsQuery.isError) {
    return <EmptyState title="Agent 调用读取失败" description="请刷新步骤详情后重试。" compact />;
  }
  if (!calls.length) {
    return (
      <EmptyState
        title="该步骤没有 Agent 调用记录"
        description={stepName.includes('agent')
          ? '调用尚未发生，或旧运行没有 Provider 审计。'
          : '只有 locate / repair / review 三个 Agent 步骤会产生模型调用。'}
        compact
      />
    );
  }

  const attempts = Array.from(new Set(calls.map((call) => call.attempt))).sort((a, b) => a - b);
  const currentAttempt = selected?.attempt ?? attempt ?? attempts[0];

  return (
    <div className="agent-trace">
      <div className="agent-trace-head">
        <div className="agent-call-switch">
          <span className="agent-call-label">调用记录</span>
          {calls.map((call) => (
            <button
              key={call.model_call_id}
              type="button"
              className={'agent-call-chip' + (call.model_call_id === selected?.model_call_id ? ' is-active' : '')}
              onClick={() => setSelectedId(call.model_call_id)}
              title={call.model_call_id}
            >
              #{shortId(call.model_call_id, 6)} · {callStatusLabel(call.status)}
            </button>
          ))}
        </div>
        {attempts.length > 1 && (
          <Select
            size="small"
            value={currentAttempt}
            style={{ width: 140 }}
            options={attempts.map((value) => ({ value, label: `attempt ${value}` }))}
            onChange={(value) => {
              const target = [...calls].reverse().find((call) => call.attempt === value);
              if (target) setSelectedId(target.model_call_id);
            }}
          />
        )}
      </div>

      {!selected ? null : detail.isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : detail.isError ? (
        <EmptyState title="调用详情读取失败" description="调用记录已找到，但提示词和工具事件无法加载。" compact />
      ) : (
        <>
          <div className="agent-call-summary">
            <span>调用 #{shortId(selected.model_call_id, 6)}</span>
            <span>attempt {selected.attempt}</span>
            <span>{role}</span>
            <span>{formatTokens(selected.total_tokens)} tokens</span>
            <span>{formatDuration(selected.duration_ms)}</span>
            <span className={selected.status === 'succeeded' ? 'text-success' : selected.status === 'failed' ? 'text-danger' : ''}>
              {callStatusLabel(selected.status)}
            </span>
          </div>
          <div className="agent-section">
            <h4>提示词</h4>
            <PromptViewer snapshot={detail.data?.prompt_snapshot} />
          </div>
          <Divider />
          <div className="agent-section">
            <h4>工具调用 <small>{selected.tool_call_count ?? detail.data?.event_total ?? 0} 次</small></h4>
            <ToolCallTimeline events={detail.data?.events ?? selected.events ?? []} />
          </div>
          <Divider />
          <div className="agent-section">
            <h4>模型输出</h4>
            <ModelOutputPanel call={detail.data?.call ?? selected} />
          </div>
        </>
      )}
    </div>
  );
}
