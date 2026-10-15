import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Empty, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import { WorkflowGraph } from '../components/WorkflowGraph';
import type { StepRecord } from '../lib/types';
const { Title, Text } = Typography;
function duration(value?: number | null) { if (value == null) return '—'; return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms'; }
export function RunDetailPage() {
  const { runId = '' } = useParams(); const navigate = useNavigate(); const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), refetchInterval: (q) => ['running', 'queued', 'waiting_approval'].includes(q.state.data?.status ?? '') ? 5000 : false });
  const resume = useMutation({ mutationFn: () => api.resumeRun(runId), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, });
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, });
  const data = detail.data;
  const stepColumns = [
    { title: '节点', dataIndex: 'title', key: 'title', render: (v: string, row: StepRecord) => <div><strong>{v || row.name}</strong><div className="secondary-small">{row.name}</div></div> },
    { title: '执行状态', dataIndex: 'status', key: 'status', render: (v: StepRecord['status']) => <StatusTag status={v} /> },
    { title: '业务结论', dataIndex: 'result', key: 'result', render: (v?: string | null) => v ? <Tag>{v}</Tag> : '—' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: duration },
    { title: '尝试次数', dataIndex: 'attempt', key: 'attempt', render: (v?: number) => v ?? 1 },
    { title: '摘要', dataIndex: 'summary', key: 'summary', render: (v?: string) => v || '—' },
    { title: '错误', dataIndex: 'error', key: 'error', render: (v?: StepRecord['error']) => v ? <span className="text-danger">{v.code}: {v.message}</span> : '—' },
  ];
  if (detail.isLoading) return <div className="page-loading"><Spin size="large" /><Text type="secondary">正在加载执行详情…</Text></div>;
  if (detail.isError || !data) return <div className="page-stack"><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>返回执行列表</Button><Alert type="error" showIcon message="无法加载执行详情" description={(detail.error as Error)?.message ?? '执行不存在或无权访问'} action={<Button onClick={() => detail.refetch()}>重试</Button>} /></div>;
  return <div className="page-stack">
    <div className="page-heading"><div><Space wrap><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>返回</Button><Title level={3} className="run-detail-title">执行详情</Title><StatusTag status={data.status} /></Space><Text type="secondary" className="run-id">{data.run_id}</Text></div>
      <Space wrap><Button icon={<ReloadOutlined />} onClick={() => detail.refetch()} loading={detail.isFetching}>刷新</Button>{data.checkpoint?.resumable && <Button type="primary" onClick={() => resume.mutate()} loading={resume.isPending}>恢复执行</Button>}{['running', 'queued'].includes(data.status) && <Button danger onClick={() => cancel.mutate()} loading={cancel.isPending}>请求停止</Button>}</Space>
    </div>
    {resume.isError && <Alert type="error" showIcon message="恢复执行失败" description={(resume.error as Error).message} />}
    {cancel.isError && <Alert type="error" showIcon message="停止请求失败" description={(cancel.error as Error).message} />}
    <Card className="content-card"><Descriptions column={{ xs: 1, sm: 2, xl: 4 }} size="small">
      <Descriptions.Item label="开始时间">{dayjs(data.started_at).isValid() ? dayjs(data.started_at).format('YYYY-MM-DD HH:mm:ss') : '—'}</Descriptions.Item>
      <Descriptions.Item label="结束时间">{data.finished_at && dayjs(data.finished_at).isValid() ? dayjs(data.finished_at).format('YYYY-MM-DD HH:mm:ss') : '执行中'}</Descriptions.Item>
      <Descriptions.Item label="任务总数">{data.task_count}</Descriptions.Item>
      <Descriptions.Item label="任务结果"><span className="text-success">{data.succeeded_tasks} 成功</span> / <span className="text-danger">{data.failed_tasks} 失败</span> / {data.waiting_tasks} 等待</Descriptions.Item>
    </Descriptions></Card>
    <Card title="工作流执行图" className="content-card" extra={<Text type="secondary">节点状态以服务端记录为准</Text>}><WorkflowGraph steps={data.steps} /></Card>
    <Card className="content-card"><Tabs items={[
      { key: 'steps', label: '节点记录', children: <Table rowKey="name" columns={stepColumns} dataSource={data.steps} pagination={false} expandable={{ expandedRowRender: (row) => <div className="step-expanded"><div><strong>输入摘要</strong><pre>{JSON.stringify(row.input ?? {}, null, 2)}</pre></div><div><strong>输出摘要</strong><pre>{JSON.stringify(row.output ?? {}, null, 2)}</pre></div></div> }} locale={{ emptyText: '暂无节点记录' }} /> },
      { key: 'tasks', label: '任务列表（' + data.tasks.length + '）', children: data.tasks.length ? <Table rowKey="task_id" dataSource={data.tasks} pagination={false} columns={[{ title: '任务 ID', dataIndex: 'task_id', key: 'task_id' }, { title: '运输商', dataIndex: 'carrier', key: 'carrier' }, { title: '业务类型', dataIndex: 'biztype', key: 'biztype' }, { title: '结论', dataIndex: 'result', key: 'result', render: (v: string) => <Tag>{v}</Tag> }, { title: '节点数', key: 'steps', render: (_: unknown, row: { steps: StepRecord[] }) => row.steps?.length ?? 0 }]} /> : <Empty description="本轮尚无任务" /> },
      { key: 'artifacts', label: '产物（' + data.artifacts.length + '）', children: data.artifacts.length ? <Table rowKey="path" dataSource={data.artifacts} pagination={false} columns={[{ title: '名称', dataIndex: 'name', key: 'name' }, { title: '说明', dataIndex: 'description', key: 'description' }, { title: '类型', dataIndex: 'media_type', key: 'media_type' }, { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', render: (v?: number) => v == null ? '—' : (v / 1024).toFixed(1) + ' KB' }]} /> : <Empty description="暂无产物" /> },
      { key: 'config', label: '执行配置', children: <pre className="json-panel">{JSON.stringify(data.config, null, 2)}</pre> },
    ]} /></Card>
  </div>;
}