import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Input, Progress, Select, Space, Table, Typography } from 'antd';
import { ArrowRightOutlined, CloseCircleOutlined, ClockCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ExecutionLaunchDialog } from '../features/executions/ExecutionLaunchDialog';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunStatus, RunSummary } from '../lib/types';

const { Title, Text } = Typography;
const validStatuses: RunStatus[] = ['queued', 'running', 'waiting_approval', 'succeeded', 'partial_failed', 'failed', 'cancelled', 'paused', 'pause_requested', 'cancel_requested'];
const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '执行中' },
  { value: 'pause_requested', label: '暂停请求中' },
  { value: 'paused', label: '已暂停' },
  { value: 'cancel_requested', label: '停止请求中' },
  { value: 'waiting_approval', label: '待最终审批' },
  { value: 'succeeded', label: '已完成' },
  { value: 'partial_failed', label: '部分失败' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
];
function formatDate(value?: string | null) {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : value;
}

export function RunsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState(() => searchParams.get('keyword') ?? '');
  const firstStatus = searchParams.get('status');
  const [status, setStatus] = useState<RunStatus | undefined>(firstStatus && validStatuses.includes(firstStatus as RunStatus) ? firstStatus as RunStatus : undefined);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    const queryKeyword = searchParams.get('keyword') ?? '';
    setKeyword((current) => current === queryKeyword ? current : queryKeyword);
    const queryStatus = searchParams.get('status');
    const nextStatus = queryStatus && validStatuses.includes(queryStatus as RunStatus) ? queryStatus as RunStatus : undefined;
    setStatus((current) => current === nextStatus ? current : nextStatus);
    if (searchParams.get('new') !== '1') return;
    setModalOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('new');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const runs = useQuery({
    queryKey: ['runs', page, keyword, status],
    queryFn: () => api.runs({ page, page_size: 20, keyword: keyword.trim(), status }),
    refetchInterval: 15000,
  });
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });

  const clearFilters = () => {
    setKeyword('');
    setStatus(undefined);
    setPage(1);
    setSearchParams({}, { replace: true });
  };
  const columns = [
    {
      title: '执行轮次',
      dataIndex: 'run_id',
      key: 'run_id',
      render: (value: string, row: RunSummary) => (
        <button className="table-identity run-list-identity" onClick={() => navigate('/runs/' + encodeURIComponent(value))}>
          <span className="table-identity-mark"><ClockCircleOutlined /></span>
          <span><strong>{value}</strong><small>{row.task_count} 个任务 · 发起人 {row.operator || '系统'}</small></span>
        </button>
      ),
    },
    { title: '运行状态', dataIndex: 'status', key: 'status', render: (value: RunSummary['status']) => <StatusTag status={value} /> },
    {
      title: '任务进度',
      key: 'progress',
      render: (_: unknown, row: RunSummary) => {
        const done = row.succeeded_tasks + row.failed_tasks;
        const percent = row.task_count > 0 ? Math.min(100, Math.round(done / row.task_count * 100)) : 0;
        return (
          <div className="run-progress-cell">
            <div><span>{done} / {row.task_count} 已结束</span><span>{percent}%</span></div>
            <Progress percent={percent} showInfo={false} size="small" />
            <small className="run-progress-detail"><span className="text-success">{row.succeeded_tasks} 成功</span><span className="text-danger">{row.failed_tasks} 失败</span><span>{row.waiting_tasks} 待处理</span></small>
          </div>
        );
      },
    },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', render: formatDate },
    { title: '结束时间', dataIndex: 'finished_at', key: 'finished_at', render: formatDate },
    {
      title: '',
      key: 'actions',
      render: (_: unknown, row: RunSummary) => <Button type="text" icon={<ArrowRightOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>打开执行</Button>,
    },
  ];

  return (
    <div className="page-stack runs-page">
      <section className="runs-hero">
        <div>
          <span className="hero-kicker"><span className="hero-kicker-dot" /> 执行控制 / 运行目录</span>
          <Title level={2}>执行记录</Title>
          <Text>查询每一轮异常扫描的运行状态、任务进度和最终结果。打开详情可查看工作流节点、控制操作与审计产物。</Text>
        </div>
        <div className="runs-hero-actions">
          <div className="runs-record-count"><strong>{runs.data?.total ?? '—'}</strong><span>匹配的执行轮次</span></div>
          <Button type="primary" size="large" icon={<PlusOutlined />} disabled={Boolean(health.data && !health.data.source_repo_configured)} onClick={() => setModalOpen(true)}>新建扫描</Button>
        </div>
      </section>

      {health.data && !health.data.source_repo_configured && <Alert type="warning" showIcon message="尚未配置源代码仓库，当前无法启动新执行" description="请先完成 AI_REPAIR_SOURCE_REPO 配置。已有执行记录仍可查询和审计。" />}
      {health.isError && <Alert type="error" showIcon message="无法确认工作流服务状态" description={(health.error as Error).message} action={<Button type="link" onClick={() => health.refetch()}>重试连接</Button>} />}
      {runs.isError && <Alert type="error" showIcon message="执行记录加载失败" description={(runs.error as Error).message} action={<Button type="link" onClick={() => runs.refetch()}>重试</Button>} />}

      <Card className="content-card runs-table-card">
        <div className="runs-filter-bar">
          <div className="runs-filter-title"><strong>所有执行轮次</strong><Text type="secondary">按关键词与运行状态筛选</Text></div>
          <div className="runs-filter-controls">
            <Input allowClear prefix={<SearchOutlined />} placeholder="搜索执行 ID / 关键词" value={keyword}
              onChange={(event) => { setKeyword(event.target.value); setPage(1); }} />
            <Select allowClear placeholder="全部状态" value={status}
              onChange={(value) => { setStatus(value); setPage(1); }}
              options={statusOptions} />
            <Button icon={<CloseCircleOutlined />} disabled={!keyword && !status} onClick={clearFilters}>清除</Button>
            <Button icon={<ReloadOutlined />} onClick={() => runs.refetch()} loading={runs.isFetching}>刷新</Button>
          </div>
        </div>
        <div className="runs-table-meta">
          <span><i className={'status-dot ' + (runs.isError ? 'is-offline' : 'is-online')} /> 每 15 秒更新一次</span>
          <span>服务端返回 {runs.data?.items.length ?? 0} 条 · 总计 {runs.data?.total ?? 0} 条</span>
        </div>
        <Table rowKey="run_id" loading={runs.isLoading} columns={columns} dataSource={runs.data?.items ?? []}
          onRow={(row) => ({ onClick: () => navigate('/runs/' + encodeURIComponent(row.run_id)), className: 'clickable-row' })}
          pagination={{ current: page, pageSize: 20, total: runs.data?.total ?? 0, showSizeChanger: false, onChange: setPage, showTotal: (total) => '共 ' + total + ' 条记录' }}
          scroll={{ x: 1020 }}
          locale={{ emptyText: <Empty description={keyword || status ? '没有符合条件的执行记录' : '还没有执行记录。创建一次异常扫描后，结果会显示在这里。'} /> }} />
      </Card>
      <ExecutionLaunchDialog open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}
