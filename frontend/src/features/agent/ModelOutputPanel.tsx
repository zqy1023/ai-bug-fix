import type { ModelCall } from '../../lib/types';
import { formatDuration, formatTokens } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';

function statusLabel(status: string): string {
  return ({
    succeeded: '成功',
    failed: '失败',
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

export function ModelOutputPanel({ call }: { call: ModelCall }) {
  return (
    <div className="model-output">
      <div className="model-metrics">
        <div><span>模型</span><strong>{call.model || '—'}</strong></div>
        <div><span>状态</span><strong className={statusClass(call.status)}>{statusLabel(call.status)}</strong></div>
        <div><span>输入 tokens</span><strong>{formatTokens(call.input_tokens)}</strong></div>
        <div><span>输出 tokens</span><strong>{formatTokens(call.output_tokens)}</strong></div>
        <div><span>总 tokens</span><strong>{formatTokens(call.total_tokens)}</strong></div>
        <div><span>耗时</span><strong>{formatDuration(call.duration_ms)}</strong></div>
        <div><span>工具调用</span><strong>{call.tool_call_count ?? 0}</strong></div>
        <div><span>事件数</span><strong>{call.event_count ?? 0}</strong></div>
      </div>
      {call.error_type && <div className="model-error">错误类型：{call.error_type}</div>}
      {call.output_preview ? (
        <pre className="model-preview">{call.output_preview}</pre>
      ) : (
        <EmptyState title="没有模型输出预览" description="Provider 未返回文本结果，或审计未记录预览。" compact />
      )}
    </div>
  );
}
