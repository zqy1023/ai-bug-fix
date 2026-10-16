import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Col, Empty, Form, InputNumber, Modal, Row, Select, Skeleton, Statistic, Table, Typography } from 'antd';
import { CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunSummary } from '../lib/types';

const { Title, Text } = Typography;
type LaunchValues = { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number };
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false }); }

export function DashboardPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const client = useQueryClient();
  const [launchOpen, setLaunchOpen] = useState(false);
  const [form] = Form.useForm<LaunchValues>();
  const query = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 });
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const start = useMutation({
    mutationFn: api.startRun,
    onSuccess: async ({ run_id }) => {
      setLaunchOpen(false);
      message.success('执行已创建，正在进入执行工作区');
      await Promise.all([client.invalidateQueries({ queryKey: ['dashboard'] }), client.invalidateQueries({ queryKey: ['runs'] })]);
      navigate('/runs/' + encodeURIComponent(run_id));
    },
    onError: (error) => message.error('创建执行失败：' + (error as Error).message),
  });
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
    { title: '执行轮次', dataIndex: 'run_id', key: 'run_id', render: (v: string) => <a onClick={() => navigate('/runs/' + encodeURIComponent(v))}>{v}</a> },
    { title: '当前状态', dataIndex: 'status', key: 'status', render: (v: RunSummary['status']) => <StatusTag status={v} /> },
    { title: '处理进度', key: 'progress', render: (_: unknown, row: RunSummary) => <span>{row.succeeded_tasks}/{row.task_count} 已成功{row.failed_tasks ? ' · ' + row.failed_tasks + ' 失败' : ''}{row.manual_tasks ? ' · ' + row.manual_tasks + ' 待人工处理' : ''}{row.waiting_tasks ? ' · ' + row.waiting_tasks + ' 待审批' : ''}</span> },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', render: formatDate },
    { title: '发起人', dataIndex: 'operator', key: 'operator', render: (v?: string) => v || '系统' },
    { title: '下一步', key: 'action', render: (_: unknown, row: RunSummary) => <Button type="link" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>继续处理</Button> },
  ];
  return <div className="page-stack workbench-home">
    <div className="page-heading">
      <div><Title level={3}>工作台</Title><Text type="secondary">先处理需要你关注的事项，再查看运行趋势和执行审计。</Text></div>
      <SpaceFallback />
    </div>
    {health.data && !health.data.source_repo_configured && <Alert type="warning" showIcon message="尚未满足启动条件" description="服务可连接，但没有配置 AI_REPAIR_SOURCE_REPO。请先完成源仓库配置，避免创建后才发现任务无法启动。" />}
    {health.isError && <Alert type="error" showIcon message="无法确认工作流服务状态" description={(health.error as Error).message} action={<Button type="link" onClick={() => health.refetch()}>重试连接</Button>} />}
    {query.isError && <Alert type="error" showIcon message="无法加载工作台数据" description={(query.error as Error).message} action={<Button type="link" onClick={() => query.refetch()}>重试</Button>} />}
    <div className="workbench-welcome">
      <div><span className="welcome-eyebrow">异常修复工作流</span><h2>今天需要关注什么？</h2><p>从异常扫描开始，跟踪每个运输商任务，只有在需要你决策时才进入人工处理。</p></div>
      <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => { form.setFieldsValue({ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }); setLaunchOpen(true); }}>开始异常扫描</Button>
    </div>
    <section className="attention-section">
      <div className="section-heading"><div><Title level={5}>处理队列</Title><Text type="secondary">点击数字直接进入对应工作区</Text></div><Button type="text" icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>刷新数据</Button></div>
      <Row gutter={[14, 14]}>
        <Col xs={12} xl={6}><Card className="attention-card attention-blue" hoverable onClick={() => navigate('/runs?status=running')}><div className="attention-top"><PlayCircleOutlined /><span>执行中</span></div><Statistic value={data?.active_runs ?? 0} loading={query.isLoading} /><Text type="secondary">查看执行进度与异常</Text></Card></Col>
        <Col xs={12} xl={6}><Card className="attention-card attention-amber" hoverable onClick={() => navigate('/approvals')}><div className="attention-top"><SafetyCertificateOutlined /><span>待审批</span></div><Statistic value={data?.waiting_approvals ?? 0} loading={query.isLoading} /><Text type="secondary">审查变更并作出决定</Text></Card></Col>
        <Col xs={12} xl={6}><Card className="attention-card attention-red"><div className="attention-top"><ExclamationCircleOutlined /><span>24 小时失败任务</span></div><Statistic value={data?.failed_24h ?? 0} loading={query.isLoading} /><Text type="secondary">定位失败原因与后续动作</Text></Card></Col>
        <Col xs={12} xl={6}><Card className="attention-card attention-green"><div className="attention-top"><CheckCircleOutlined /><span>24 小时成功任务</span></div><Statistic value={data?.succeeded_24h ?? 0} loading={query.isLoading} /><Text type="secondary">查看已完成的修复结果</Text></Card></Col>
      </Row>
    </section>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}><Card title="近 7 天处理趋势" extra={<Text type="secondary">任务成功率 {((data?.success_rate_7d ?? 0) * 100).toFixed(1)}%</Text>} className="content-card">
        {query.isLoading ? <Skeleton active /> : data?.daily_trend.length ? <ReactECharts option={option} style={{ height: 290 }} notMerge /> : <Empty description="暂无趋势数据" />}
      </Card></Col>
      <Col xs={24} xl={8}><Card title="工作流服务状态" className="content-card service-card">
        <div className="service-state"><span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} /><strong>{health.isSuccess ? '服务连接正常' : health.isError ? '服务连接异常' : '正在检查服务'}</strong></div>
        <div className="health-row"><span>源代码仓库</span><strong>{health.data ? (health.data.source_repo_configured ? '已配置' : '未配置') : '—'}</strong></div>
        <div className="health-row"><span>运行目录</span><strong>{health.data ? (health.data.runtime_dir_configured ? '已配置' : '未配置') : '—'}</strong></div>
        <div className="health-row"><span>近 7 天成功率</span><strong>{((data?.success_rate_7d ?? 0) * 100).toFixed(1)}%</strong></div>
        <Text type="secondary" className="health-note">启动前置条件以服务端健康检查为准。前端状态提示不替代服务端校验。</Text>
      </Card></Col>
    </Row>
    <Card title="最近执行" className="content-card" extra={<Button type="link" onClick={() => navigate('/runs')}>查看全部执行 →</Button>}>
      {query.isLoading ? <Skeleton active /> : <Table rowKey="run_id" columns={columns} dataSource={data?.recent_runs ?? []} pagination={false} locale={{ emptyText: '还没有执行记录。可以从页面右上方开始一次异常扫描。' }} />}
    </Card>
    <Modal title="开始异常扫描" open={launchOpen} onCancel={() => !start.isPending && setLaunchOpen(false)} onOk={() => form.submit()} confirmLoading={start.isPending} okText="确认并开始" cancelText="返回修改" destroyOnClose>
      <Alert type="info" showIcon message="启动后任务按工作流规则串行处理" description="这一步只创建执行，不会直接提交或合并代码。遇到共享层变更、证据不足或审查风险时，工作流会进入相应门禁。" style={{ marginBottom: 20 }} />
      <Form form={form} layout="vertical" onFinish={(values) => start.mutate(values)} initialValues={{ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }}>
        <Form.Item name="log_days" label="扫描最近多少天的日志" rules={[{ required: true }, { type: 'number', min: 1, max: 30, message: '范围为 1–30 天' }]}><InputNumber min={1} max={30} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="log_limit" label="日志数量上限" extra="0 表示采用服务端配置的默认策略" rules={[{ required: true }, { type: 'number', min: 0, max: 10000 }]}><InputNumber min={0} max={10000} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="review_mode" label="变更审查方式" rules={[{ required: true }]}><Select options={[{ value: 'agent', label: 'AI 审查，必要时转人工' }, { value: 'human', label: '所有任务强制人工审查' }]} /></Form.Item>
        <Form.Item name="max_attempts" label="每个任务最多修复尝试次数" rules={[{ required: true }, { type: 'number', min: 1, max: 5, message: '范围为 1–5 次' }]}><InputNumber min={1} max={5} precision={0} style={{ width: '100%' }} /></Form.Item>
      </Form>
    </Modal>
  </div>;
}

function SpaceFallback() {
  return <Text type="secondary" className="dashboard-refresh-note">状态每 15 秒自动更新</Text>;
}
