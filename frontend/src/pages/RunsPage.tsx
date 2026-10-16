import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Select, Table, Typography } from 'antd';
import { CloseCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ExecutionLaunchDialog } from '../features/executions/ExecutionLaunchDialog';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunStatus, RunSummary } from '../lib/types';
const { Title, Text } = Typography;
export function RunsPage() {
  const navigate = useNavigate(); const [searchParams] = useSearchParams();
  const initialStatus = searchParams.get('status');
  const [page, setPage] = useState(1); const [keyword, setKeyword] = useState(''); const [status, setStatus] = useState<RunStatus | undefined>(initialStatus && ['queued', 'running', 'waiting_approval', 'succeeded', 'partial_failed', 'failed', 'cancelled', 'paused', 'pause_requested', 'cancel_requested'].includes(initialStatus) ? initialStatus as RunStatus : undefined); const [modalOpen, setModalOpen] = useState(false);
  const runs = useQuery({ queryKey: ['runs', page, keyword, status], queryFn: () => api.runs({ page, page_size: 20, keyword, status }), refetchInterval: 15000 });
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const columns = [
    { title: '执行 ID', dataIndex: 'run_id', key: 'run_id', render: (v: string) => <a onClick={() => navigate('/runs/' + encodeURIComponent(v))}>{v}</a> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: RunSummary['status']) => <StatusTag status={v} /> },
    { title: '任务进度', key: 'progress', render: (_: unknown, row: RunSummary) => <span>{row.succeeded_tasks}/{row.task_count} 完成 · {row.failed_tasks} 失败 · {row.manual_tasks ?? 0} 人工介入</span> },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '结束时间', dataIndex: 'finished_at', key: 'finished_at', render: (v?: string | null) => v && dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '发起人', dataIndex: 'operator', key: 'operator', render: (v?: string) => v || '系统' },
    { title: '操作', key: 'actions', render: (_: unknown, row: RunSummary) => <Button type="link" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>查看详情</Button> },
  ];
  return <div className="page-stack">
    <div className="page-heading"><div><Title level={3}>执行工作区</Title><Text type="secondary">按状态找到需要处理的轮次；打开后优先查看任务结论、根因证据与下一步操作。</Text></div><Button type="primary" icon={<PlusOutlined />} disabled={Boolean(health.data && !health.data.source_repo_configured)} onClick={() => setModalOpen(true)}>新建执行</Button></div>
    {health.data && !health.data.source_repo_configured && <Alert type="warning" showIcon message="尚未配置源代码仓库，当前无法启动新执行" description="请先完成 AI_REPAIR_SOURCE_REPO 配置。已有执行记录仍可查询和审计。" />}
    {health.isError && <Alert type="error" showIcon message="无法确认工作流服务状态" description={(health.error as Error).message} action={<Button type="link" onClick={() => health.refetch()}>重试连接</Button>} />}
    {runs.isError && <Alert type="error" showIcon message="执行记录加载失败" description={(runs.error as Error).message} action={<Button type="link" onClick={() => runs.refetch()}>重试</Button>} />}
    <Card className="content-card"><div className="filter-row">
      <Input allowClear prefix={<SearchOutlined />} placeholder="搜索执行 ID" value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1); }} style={{ maxWidth: 300 }} />
      <Select allowClear placeholder="执行状态" value={status} onChange={(v) => { setStatus(v); setPage(1); }} style={{ width: 170 }} options={[{ value: 'queued', label: '排队中' }, { value: 'running', label: '执行中' }, { value: 'pause_requested', label: '暂停请求中' }, { value: 'paused', label: '已暂停' }, { value: 'cancel_requested', label: '停止请求中' }, { value: 'waiting_approval', label: '待审批' }, { value: 'succeeded', label: '已完成' }, { value: 'partial_failed', label: '部分失败' }, { value: 'failed', label: '失败' }, { value: 'cancelled', label: '已取消' }]} />
      <Button icon={<CloseCircleOutlined />} disabled={!keyword && !status} onClick={() => { setKeyword(''); setStatus(undefined); setPage(1); }}>清除筛选</Button><Button icon={<ReloadOutlined />} onClick={() => runs.refetch()} loading={runs.isFetching}>刷新</Button><Text type="secondary">每 15 秒自动更新</Text>
    </div><Table rowKey="run_id" loading={runs.isLoading} columns={columns} dataSource={runs.data?.items ?? []} pagination={{ current: page, pageSize: 20, total: runs.data?.total ?? 0, showSizeChanger: false, onChange: setPage, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '没有符合条件的执行记录' }} /></Card>
    <ExecutionLaunchDialog open={modalOpen} onClose={() => setModalOpen(false)} />
  </div>;
}