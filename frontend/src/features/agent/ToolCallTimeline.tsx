import { useState } from 'react';
import { CheckCircleFilled, CloseCircleFilled, ToolFilled } from '@ant-design/icons';
import type { AgentEvent } from '../../lib/types';
import { EmptyState } from '../../components/EmptyState';
import { formatDuration } from '../../lib/format';

type Call = {
  tool: string;
  input: Record<string, unknown>;
  elapsedMs?: number;
  result?: string;
  isError?: boolean;
  durationMs?: number;
};

function pairEvents(events: AgentEvent[]): Call[] {
  const calls: Call[] = [];
  for (const event of events) {
    if (event.type === 'tool_use') {
      calls.push({ tool: event.tool ?? 'unknown', input: event.input ?? {}, elapsedMs: event.elapsed_ms });
    } else if (event.type === 'tool_result' && calls.length) {
      const target = calls[calls.length - 1];
      target.result = event.text;
      target.isError = event.is_error;
      target.durationMs = event.elapsed_ms;
    }
  }
  return calls;
}

/** 工具调用时间线：结果默认截断为 3 行，避免长输出淹没追踪视图。 */
export function ToolCallTimeline({ events }: { events: AgentEvent[] }) {
  const calls = pairEvents(events);
  const [openResult, setOpenResult] = useState<Record<number, boolean>>({});

  if (!calls.length) {
    return <EmptyState title="没有工具调用" description="本次模型调用未使用工具，或审计未记录工具事件。" compact />;
  }

  const toggle = (index: number) => setOpenResult((current) => ({ ...current, [index]: !current[index] }));

  return (
    <ol className="tool-timeline">
      {calls.map((call, index) => {
        const expanded = Boolean(openResult[index]);
        const longResult = (call.result?.length ?? 0) > 220;
        return (
          <li key={`${call.tool}-${index}`} className={'tool-item' + (call.isError ? ' is-error' : '')}>
            <span className="tool-icon"><ToolFilled /></span>
            <div className="tool-body">
              <div className="tool-head">
                <strong>{call.tool}</strong>
                {call.input && Object.keys(call.input).length > 0 && (
                  <span className="tool-input">
                    {Object.entries(call.input).slice(0, 4).map(([key, value]) => (
                      <code key={key}>{key}={String(value).slice(0, 80)}</code>
                    ))}
                  </span>
                )}
                <span className="tool-duration">{formatDuration(call.durationMs ?? call.elapsedMs)}</span>
                {call.isError ? <CloseCircleFilled className="tool-status is-error" /> : <CheckCircleFilled className="tool-status" />}
              </div>
              {call.result && (
                <>
                  <pre className={'tool-result' + (expanded ? ' is-open' : '')}>{call.result}</pre>
                  {longResult && (
                    <button type="button" className="tool-result-toggle" onClick={() => toggle(index)}>
                      {expanded ? '收起结果' : `展开结果（${call.result.length} 字）`}
                    </button>
                  )}
                </>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
