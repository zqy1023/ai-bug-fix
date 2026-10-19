import { Collapse, Empty, Tag, Typography } from 'antd';
import type { ModelCall } from '../lib/types';

const { Text } = Typography;

export function AgentEventTimeline({ calls }: { calls: ModelCall[] }) {
  const items = calls.flatMap((call) => (call.events ?? []).map((event, index) => ({
    key: `${call.model_call_id}:${index}`,
    label: `${call.agent_role} · ${event.type}`,
    children: (
      <div className="agent-event">
        <div className="agent-event-head"><Tag color={event.is_error ? 'error' : event.type === 'tool_use' ? 'blue' : event.type === 'tool_result' ? 'default' : 'purple'}>{event.type}</Tag><Text type="secondary">{call.model}</Text></div>
        {event.tool && <Text strong>工具：{event.tool}</Text>}
        {event.text && <div className="agent-event-text">{event.text}</div>}
        {event.input && <pre className="json-panel">{JSON.stringify(event.input, null, 2)}</pre>}
        {event.elapsed_ms != null && <Text type="secondary">耗时 {event.elapsed_ms} ms</Text>}
      </div>
    ),
  })));
  return items.length ? <Collapse items={items} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可展示的 Agent 事件" />;
}
