import { useMemo, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Input, Select, Skeleton, Table, Typography } from 'antd';
import { ArrowRightOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunSummary, TaskSummary } from '../lib/types';

const { Title, Text } = Typography;
type AuditedTask = TaskSummary & { run_id: string; run_status: RunSummary['status']; started_at: string };

export function TaskAuditPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [result, setResult] = useState<string>();
  const runs = useQuery({ queryKey: ['task-audit-runs'], queryFn: () => api.runs({ page: 1, page_size: 30 }), refetchInterval: 15000 });
  const details = useQueries({ queries: (runs.data?.items ?? []).map((run) => ({
    queryKey: ['task-audit-run', run.run_id],
    queryFn: () => api.run(run.run_id),
    staleTime: 10000,
    retry: 1,
  })) });
  const tasks = useMemo<AuditedTask[]>(() => details.flatMap((query, index) => {
    const run = runs.data?.items[index];
    if (!run || !query.data) return [];
    return query.data.tasks.map((task) => ({ ...task, run_id: run.run_id, run_status: run.status, started_at: run.started_at }));
  }), [details, runs.data?.items]);
  const filtered = tasks.filter((task) => {
    const search = keyword.trim().toLowerCase();
    const matchesKeyword = !search || [task.task_id, task.run_id, task.carrier, task.biztype, task.terminal_reason, task.problem ? JSON.stringify(task.problem) : ''].some((value) => String(value ?? '').toLowerCase().includes(search));
    const matchesResult = !result || [task.result, task.terminal_status, task.run_status].some((value) => String(value ?? '').toLowerCase() === result.toLowerCase());
    return matchesKeyword && matchesResult;
  });
  const columns = [
    { title: '任务 ID', dataIndex: 'task_id', key: 'task_id', render: (value: string, task: AuditedTask) => <button className="table-identity" onClick={() => navigate('/runs/' + encodeURIComponent(task.run_id) + '/tasks/' + encodeURIComponent(value))}><span><strong>{value}</strong><small>{task.run_id}</small></span></button> },
    { title: '运输商', dataIndex: 'carrier', key: 'carrier', render: (value: string) => value || '—' },
    { title: '业务类型', dataIndex: 'biztype', key: 'biztype', render: (value: string) => value || '—' },
    { title: '业务结果', dataIndex: 'result', key: 'result', render: (value: string, task: AuditedTask) => <StatusTag status={value || task.terminal_status || task.run_status} /> },
    { title: '终态步骤', dataIndex: 'terminal_step', key: 'terminal_step', render: (value: string) => value || '—' },
    { title: '终态原因', dataIndex: 'terminal_reason', key: 'terminal_reason', render: (value: string) => <span className="audit-reason">{value || '—'}</span> },
    { title: '运行轮次', dataIndex: 'run_id', key: 'run_id', render: (value: string) => <Button type="link" onClick={() => navigate('/runs/' + encodeURIComponent(value))}>查看运行 <ArrowRightOutlined /></Button> },
  ];
  const isLoading = runs.isLoading || details.some((query) => query.isLoading);
  return <div className="page-stack task-audit-page">
    <section className="runs-hero">
      <div><span className="hero-kicker"><span className="hero-kicker-dot" /> 工作流观测 / 任务审计</span><Title level={2}>任务审计</Title><Text>跨运行轮次检索任务终态、根因结论和后续动作。数据来自真实运行记录。</Text></div>
      <div className="runs-hero-actions"><div className="runs-record-count"><strong>{filtered.length}</strong><span>匹配任务</span></div><Button icon={<ReloadOutlined />} loading={runs.isFetching || details.some((query) => query.isFetching)} onClick={() => { void runs.refetch(); void Promise.all(details.map((query) => query.refetch())); }}>刷新</Button></div>
    </section>
    {runs.isError && <Alert showIcon type="error" message="任务审计数据加载失败" description={(runs.error as Error).message} />}
    {details.some((query) => query.isError) && <Alert showIcon type="warning" message="部分运行详情暂时不可用" description="表格仅展示成功加载的运行记录；可刷新重试。" />}
    <Card className="content-card runs-table-card">
      <div className="runs-filter-bar">
        <div className="runs-filter-title"><strong>任务记录</strong><Text type="secondary">最近 30 个运行轮次 · 每 15 秒刷新</Text></div>
        <div className="runs-filter-controls">
          <Input allowClear prefix={<SearchOutlined />} placeholder="任务 ID / 运输商 / 原因" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
          <Select allowClear placeholder="全部终态" value={result} onChange={setResult} options={[{ value: 'PASS', label: '通过' }, { value: 'FAIL', label: '失败' }, { value: 'MANUAL', label: '需人工处理' }, { value: 'REJECTED', label: '已拒绝' }, { value: 'DELIVERED', label: '已交付' }]} />
        </div>
      </div>
      {isLoading ? <Skeleton active paragraph={{ rows: 7 }} /> : <Table rowKey={(task) => task.run_id + ':' + task.task_id} columns={columns} dataSource={filtered} pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (total) => '共 ' + total + ' 条任务' }} scroll={{ x: 1150 }} locale={{ emptyText: <Empty description={keyword || result ? '没有符合条件的任务' : '最近的运行轮次中暂无任务记录'} /> }} />}
      <Text type="secondary">当前页面聚合最近 30 个运行轮次；进入任务详情可查看节点输入输出、模型调用、审计记录和产物。</Text>
    </Card>
  </div>;
}
