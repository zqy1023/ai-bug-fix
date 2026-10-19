import { Alert, Button, Space, Tag, Typography } from 'antd';
import type { RunDetail } from '../lib/types';

const { Text } = Typography;

export function CheckpointPanel({ run, onResume, loading }: { run: RunDetail; onResume?: () => void; loading?: boolean }) {
  const checkpoint = run.checkpoint;
  if (!checkpoint) return null;
  return <Alert
    className="checkpoint-panel"
    type={checkpoint.resumable ? 'info' : 'warning'}
    showIcon
    message={<Space><span>LangGraph Checkpoint</span><Tag color={checkpoint.resumable ? 'blue' : 'orange'}>{checkpoint.resumable ? '可恢复' : '不可恢复'}</Tag></Space>}
    description={<Space direction="vertical" size={4}>
      <Text type="secondary">恢复入口由服务端 checkpoint 决定，页面不从头重跑工作流。</Text>
      {checkpoint.next_nodes.length > 0 && <Text>下一节点：{checkpoint.next_nodes.join('、')}</Text>}
      {checkpoint.resumable && onResume && <Button size="small" type="primary" loading={loading} onClick={onResume}>从 checkpoint 恢复</Button>}
    </Space>}
  />;
}
