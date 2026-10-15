import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Empty, Row, Skeleton, Statistic, Table, Typography } from 'antd';
import { CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunSummary } from '../lib/types';
const { Title, Text } = Typography;
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false }); }
export function DashboardPage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 });
  const data = query.data;
  const option = {
    tooltip: { trigger: 'axis' }, legend: { data: ['成功任务', '失败任务'], bottom: 0 },
    grid: { left: 16, right: 16, top: 24, bottom: 42, containLabel: true },
    xAxis: { type: 'category', data: data?.daily_trend.map((d) => d.date) ?? [], axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#edf0f5' } } },
    series: [
      { name: '成功任务', type: 'bar', stack: 'total', data: data?.daily_trend.map((d) => d.succeeded) ?? [], itemStyle: { color: '#3f7ee8', borderRadius: [3, 3, 0, 0] } },
      { name: '失败任务', type: 'bar', stack: 'total', data: data?.daily_trend.map((d) => d.failed) ?? [], itemStyle: { color: '#f06b68', borderRadius: [3, 3, 0, 0] } },
    ],
  };
  const columns = [
    { title: '执行 ID', dataIndex: 'run_id', key: 'run_id', render: (v: string) => <a onClick={() => navigate('/runs/' + encodeURIComponent(v))}>{v}</a> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: RunSummary['status']) => <StatusTag status={v} /> },
    { title: '任务进度', key: 'progress', render: (_: unknown, row: RunSummary) => <span>{row.succeeded_tasks}/{row.task_count} 完成{row.waiting_tasks ? ' · ' + row.waiting_tasks + ' 等待' : ''}</span> },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', render: formatDate },
    { title: '发起人', dataIndex: 'operator', key: 'operator', render: (v?: string) => v || '系统' },
  ];
  return <div className="page-stack">
    <div className="page-heading"><div><Title level={3}>运行总览</Title><Text type="secondary">掌握自动修复执行、待审批事项与近期质量变化。</Text></div></div>
    {query.isError && <Alert type="error" showIcon message="无法加载运行概览" description={(query.error as Error).message} action={<Button type="link" onClick={() => query.refetch()}>重试</Button>} />}
    <Row gutter={[16, 16]}>
      <Col xs={24} sm={12} xl={6}><Card className="metric-card"><Statistic title="正在执行" value={data?.active_runs ?? 0} prefix={<PlayCircleOutlined />} loading={query.isLoading} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card className="metric-card"><Statistic title="等待人工审批" value={data?.waiting_approvals ?? 0} prefix={<ClockCircleOutlined />} valueStyle={{ color: data?.waiting_approvals ? '#d48806' : undefined }} loading={query.isLoading} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card className="metric-card"><Statistic title="24 小时成功任务" value={data?.succeeded_24h ?? 0} prefix={<CheckCircleOutlined />} loading={query.isLoading} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card className="metric-card"><Statistic title="24 小时失败任务" value={data?.failed_24h ?? 0} prefix={<ExclamationCircleOutlined />} loading={query.isLoading} /></Card></Col>
    </Row>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}><Card title="近 7 天任务结果" extra={<Text type="secondary">成功率 {((data?.success_rate_7d ?? 0) * 100).toFixed(1)}%</Text>} className="content-card">
        {query.isLoading ? <Skeleton active /> : data?.daily_trend.length ? <ReactECharts option={option} style={{ height: 300 }} notMerge /> : <Empty description="暂无趋势数据" />}
      </Card></Col>
      <Col xs={24} xl={8}><Card title="处理健康度" className="content-card health-card">
        <div className="health-value">{((data?.success_rate_7d ?? 0) * 100).toFixed(1)}<span>%</span></div><Text type="secondary">近 7 天任务成功率</Text>
        <div className="health-divider" /><div className="health-row"><span>成功任务</span><strong className="text-success">{data?.succeeded_24h ?? '—'}</strong></div>
        <div className="health-row"><span>失败任务</span><strong className="text-danger">{data?.failed_24h ?? '—'}</strong></div>
        <Text type="secondary" className="health-note">成功率以服务端统计口径为准，不由前端根据不完整数据推算。</Text>
      </Card></Col>
    </Row>
    <Card title="最近执行" className="content-card" extra={<a onClick={() => navigate('/runs')}>查看全部 →</a>}>
      {query.isLoading ? <Skeleton active /> : <Table rowKey="run_id" columns={columns} dataSource={data?.recent_runs ?? []} pagination={false} locale={{ emptyText: '暂无执行记录' }} />}
    </Card>
  </div>;
}