import { useState } from 'react';
import type { ModelCall } from '../../lib/types';
import { formatDuration, formatTokens } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';

function statusLabel(status: string): string {
  return ({
    succeeded: '调用成功',
    failed: '调用失败',
    running: '运行中',
    pending: '等待中',
    cancelled: '已取消',
  } as Record<string, string>)[status] || status || '未知';
}

function statusClass(status: string): string {
  if (status === 'succeeded') return 'text-success';
  if (status === 'failed') return 'text-danger';
  return '';
}

export function ModelOutputPanel({ call, fullOutput }: { call: ModelCall; fullOutput?: string }) {
  const [expandedCall, setExpandedCall] = useState('');
  const expanded = expandedCall === call.model_call_id;
  const archived = fullOutput || call.output_preview || '';
  const preview = archived.slice(0, 500);
  return (
    <div className="model-output">
      <div className="model-metrics">
        <div><span>配置模型</span><strong>{call.model || '—'}</strong></div>
        <div><span>状态</span><strong className={statusClass(call.status)}>{statusLabel(call.status)}</strong></div>
        <div><span>输入 tokens</span><strong>{formatTokens(call.input_tokens)}</strong></div>
        <div><span>输出 tokens</span><strong>{formatTokens(call.output_tokens)}</strong></div>
        <div><span>{call.token_total_scope === 'sdk_reported' ? 'SDK 总 tokens' : '输入＋输出 tokens'}</span><strong>{formatTokens(call.total_tokens)}</strong></div>
        <div><span>缓存读取 tokens（单列）</span><strong>{formatTokens(call.cache_tokens)}</strong></div>
        <div><span>耗时</span><strong>{formatDuration(call.duration_ms)}</strong></div>
        <div><span>工具尝试</span><strong>{call.tool_call_count ?? '未知'}</strong></div>
        <div><span>放行／拒绝／完成</span><strong>{call.tool_allowed ?? '—'}／{call.tool_denied ?? '—'}／{call.tool_completed ?? '—'}</strong></div>
        <div><span>结构化输出调用</span><strong>{call.structured_output_calls ?? '未知'}</strong></div>
        <div><span>提供方／用量来源</span><strong>{call.provider || '历史未记录'}／{call.usage_source === 'sdk_reported' ? 'SDK 回报' : '未知'}</strong></div>
        <div><span>事件数</span><strong>{call.event_count ?? 0}</strong></div>
      </div>
      <p>调用成功表示取得模型返回；业务与审查结论见步骤结果。缺失用量不计作零。</p>
      {Boolean(call.sdk_receipt?.model_usage) && <details><summary>SDK 实际模型与用量回执</summary><pre>{JSON.stringify(call.sdk_receipt, null, 2)}</pre></details>}
      {call.error_type && <div className="model-error">错误类型：{call.error_type}</div>}
      {Boolean(call.error_detail) && <details><summary>查看调用失败详情</summary><pre>{String(call.error_detail)}</pre></details>}
      {archived ? (
        <><pre className="model-preview">{expanded ? archived : preview}</pre>
          {archived.length > 500 && <button type="button" onClick={() => setExpandedCall(expanded ? '' : call.model_call_id)}>
            {expanded ? '收起输出' : `展开已归档输出（${archived.length} 字）`}
          </button>}
        </>
      ) : (
        <EmptyState title="没有模型输出预览" description="Provider 未返回文本结果，或审计未记录预览。" compact />
      )}
    </div>
  );
}
