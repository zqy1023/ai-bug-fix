import { authorizationHeaders } from './api';
import type { RunDetail } from './types';

export type StreamState = 'connecting' | 'open' | 'closed' | 'error';

type RunEventHandlers = {
  onSnapshot: (snapshot: RunDetail) => void;
  onState?: (state: StreamState, detail?: string) => void;
};

type StreamError = Error & { status?: number };

/**
 * 订阅持久化运行快照。服务端在终态主动关闭流，因此正常结束不重连；
 * 只有网络/连接错误才退避重连，避免终态后无限重连。
 */
export function subscribeRunEvents(runId: string, handlers: RunEventHandlers): () => void {
  const controller = new AbortController();
  let retryTimer: number | undefined;
  let attempts = 0;
  let disposed = false;
  let terminal = false;

  const connect = async () => {
    if (disposed) return;
    handlers.onState?.('connecting');
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}/events`, {
        headers: { Accept: 'text/event-stream', ...authorizationHeaders() },
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw Object.assign(new Error(`运行事件流连接失败（HTTP ${response.status}）`), {
          status: response.status,
        }) as StreamError;
      }
      handlers.onState?.('open');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName = '';
      let data: string[] = [];
      const dispatch = () => {
        if (eventName === 'stream.error') throw new Error('运行快照暂时不可用');
        if (eventName === 'run.snapshot' && data.length) {
          const snapshot = JSON.parse(data.join('\n')) as RunDetail;
          if (snapshot.run_id !== runId) throw new Error('运行快照身份不匹配');
          terminal = snapshot.terminal === true;
          attempts = 0;
          handlers.onSnapshot(snapshot);
        }
        eventName = '';
        data = [];
      };
      while (!controller.signal.aborted) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? '';
        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, '');
          if (!line) {
            dispatch();
          } else if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            data.push(line.slice(5).trimStart());
          }
        }
      }
      reader.releaseLock();
      if (controller.signal.aborted || disposed) return;
      if (!terminal) throw new Error('连接已断开，正在重新同步');
      handlers.onState?.('closed');
    } catch (error) {
      if (controller.signal.aborted || disposed) return;
      handlers.onState?.('error', error instanceof Error ? error.message : '运行事件流连接失败');
      if ((error as StreamError).status === 401 || (error as StreamError).status === 403) return;
      attempts += 1;
      retryTimer = window.setTimeout(() => void connect(), Math.min(3000 * attempts, 15000));
    }
  };

  void connect();
  return () => {
    disposed = true;
    controller.abort();
    if (retryTimer !== undefined) window.clearTimeout(retryTimer);
  };
}
