import { authorizationHeaders } from './api';

type RunEventHandlers = {
  onSnapshot: () => void;
  onError?: (error: Error) => void;
};

/** Subscribe to the persisted run snapshot stream while preserving Bearer auth. */
export function subscribeRunEvents(runId: string, handlers: RunEventHandlers): () => void {
  const controller = new AbortController();
  let retryTimer: number | undefined;

  const connect = async () => {
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}/events`, {
        headers: { Accept: 'text/event-stream', ...authorizationHeaders() },
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`运行事件流连接失败（HTTP ${response.status}）`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName = '';
      let data: string[] = [];
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
            data = [];
          } else if (line.startsWith('event:')) eventName = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trim());
        }
      }
      if (!controller.signal.aborted) retryTimer = window.setTimeout(connect, 3000);
    } catch (error) {
      if (controller.signal.aborted) return;
      handlers.onError?.(error instanceof Error ? error : new Error('运行事件流连接失败'));
      retryTimer = window.setTimeout(connect, 3000);
    }
  };

  void connect();
  return () => {
    controller.abort();
    if (retryTimer !== undefined) window.clearTimeout(retryTimer);
  };
}
