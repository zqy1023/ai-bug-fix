import { useState } from 'react';
import { ToolFilled } from '@ant-design/icons';
import type { AgentEvent } from '../../lib/types';
import { EmptyState } from '../../components/EmptyState';
import { StatusDot } from '../../components/StatusBadge';
import type { Tone } from '../../app/theme';
import { formatDuration } from '../../lib/format';
import styles from './ToolCallTimeline.module.css';

type Call = {
  id?: string;
  tool: string;
  input: Record<string, unknown>;
  elapsedMs?: number;
  result?: string;
  isError?: boolean;
  durationMs?: number;
  status: 'pending' | 'succeeded' | 'failed' | 'denied' | 'unlinked';
  startMs?: number;
};

export function pairEvents(events: AgentEvent[]): Call[] {
  const calls: Call[] = [];
  const byId = new Map<string, Call>();
  for (const event of events) {
    if (event.type === 'assistant') continue;
    const id = event.tool_use_id || undefined;
    let target = id ? byId.get(id) : undefined;
    if (!target) {
      target = { id, tool: event.type === 'tool_result' ? '未关联的工具结果' : event.tool || 'unknown',
        input: {}, status: id ? 'pending' : 'unlinked' };
      calls.push(target);
      if (id) byId.set(id, target);
    }
    if (event.type === 'tool_use' || event.type === 'tool_decision') {
      target.tool = event.tool || target.tool;
      if (event.type === 'tool_decision' || target.startMs === undefined) target.input = event.input || target.input;
      if (event.type === 'tool_decision') {
        target.startMs = event.elapsed_ms;
        if (event.decision === 'deny') {
          target.status = 'denied'; target.result = event.reason; target.isError = true;
        }
      }
    } else if (event.type === 'tool_result') {
      target.result = event.text;
      target.isError = target.status === 'denied' || event.is_error;
      target.elapsedMs = event.elapsed_ms;
      if (id && target.status !== 'denied') target.status = event.is_error ? 'failed' : 'succeeded';
      if (typeof target.startMs === 'number' && typeof event.elapsed_ms === 'number') {
        target.durationMs = Math.max(0, event.elapsed_ms - target.startMs);
      }
    }
  }
  return calls;
}

function callTone(call: Call): Tone {
  if (call.isError) return 'danger';
  if (call.status === 'succeeded') return 'success';
  return 'neutral';
}

/** 按 SDK 调用 ID 关联；旧归档缺 ID 时保留未关联结果。 */
export function ToolCallTimeline({ events, expectedCount }: { events: AgentEvent[]; expectedCount?: number }) {
  const calls = pairEvents(events);
  const [openResult, setOpenResult] = useState<Record<number, boolean>>({});
  const [showAll, setShowAll] = useState(false);
  if (!calls.length) {
    return <EmptyState title={expectedCount === 0 ? '本次没有工具调用' : '工具事件明细缺失'}
      description={expectedCount ? `记录有 ${expectedCount} 次工具尝试，服务端未返回对应明细。` : '暂无可关联的执行证据。'} compact />;
  }
  const labels = { pending: '结果缺失／执行中', succeeded: '已完成', failed: '执行失败', denied: '已拒绝', unlinked: '旧记录缺少调用 ID，未关联' };
  const visible = showAll ? calls : calls.slice(0, 12);
  return <div className={styles.wrap}>
    {calls.some(call => call.status === 'unlinked') && <p className={styles.notice}>旧归档缺少调用 ID，结果单独展示，不能据此判断某次读取或修改成功。</p>}
    <ol className={styles.timeline}>
      {visible.map((call, index) => <li key={`${call.id || call.tool}-${index}`} className={`${styles.item}${call.isError ? ` ${styles.itemError}` : ''}`}>
        <span className={styles.icon}><ToolFilled /></span>
        <div className={styles.body}>
          <div className={styles.head}>
            <strong>{call.tool}</strong>
            <span className={styles.input}>{Object.entries(call.input).filter(([key]) => ['file_path', 'path', 'pattern', 'offset', 'limit'].includes(key)).map(([key, value]) =>
              <code key={key} style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{key}={String(value)}</code>)}</span>
            <span className={styles.duration}>{typeof call.durationMs === 'number' ? formatDuration(call.durationMs) : '耗时未记录'}</span>
            <StatusDot tone={callTone(call)} label={labels[call.status]}>
              <span className={`${styles.status}${call.isError ? ` ${styles.statusError}` : ''}`}>{labels[call.status]}</span>
            </StatusDot>
          </div>
          {Object.keys(call.input).length > 0 && <details className={styles.details}><summary>完整调用参数</summary><pre>{JSON.stringify(call.input, null, 2)}</pre></details>}
          {call.result && <>
            <pre className={`${styles.result}${openResult[index] ? ` ${styles.resultOpen}` : ''}`}>{call.result}</pre>
            {call.result.length > 220 && <button type="button" className={styles.toggle}
              onClick={() => setOpenResult(current => ({ ...current, [index]: !current[index] }))}>
              {openResult[index] ? '收起结果' : `展开完整结果（${call.result.length} 字）`}</button>}
          </>}
        </div>
      </li>)}
    </ol>
    {calls.length > 12 && <button type="button" className={styles.more} onClick={() => setShowAll(!showAll)}>{showAll ? '收起' : `查看全部 ${calls.length} 条工具记录`}</button>}
  </div>;
}
