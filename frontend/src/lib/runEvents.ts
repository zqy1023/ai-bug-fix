import { authorizationHeaders } from './api';

export type StreamState = 'connecting' | 'open' | 'closed' | 'error';

type RunEventHandlers = {
  onSnapshot: () => void;
  onState?: (state: StreamState, detail?: string) => void;
};

/** 连接阶段失败时附加 HTTP status，供 catch 分支判断鉴权错误。 */
type StreamError = Error & { status?: number };

/**
 * 订阅持久化运行快照。服务端在终态主动关闭流，因此正常结束不重连；
 * 只有网络/连接错误才按 3s 退避重连，避免终态后无限重连。
 */
export function subscribeRunEvents(runId: string, handlers: RunEventHandlers): () => void {
  const controller = new AbortController();
  let retryTimer: number | undefined;
  let attempts = 0;
  let disposed = false;

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
        throw Object.assign(
          new Error(`运行事件流连接失败（HTTP ${response.status}）`),
          { status: response.status },
        ) as StreamError;
      }
      attempts = 0;
      handlers.onState?.('open');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName = '';
      while (!controller.signal.aborted) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line) {
            if (eventName === 'run.snapshot') handlers.onSnapshot();
            eventName = '';
          } else if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
          }
        }
      }
      if (!controller.signal.aborted) handlers.onState?.('closed');
      handlers.onSnapshot();
    } catch (error) {
      if (controller.signal.aborted || disposed) return;
      handlers.onState?.('error', error instanceof Error ? error.message : '运行事件流连接失败');
      // 401/403 属于鉴权失败，退避重连也无法恢复；停止订阅，避免无限重试循环。
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
