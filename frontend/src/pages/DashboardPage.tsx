import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Empty, Progress, Row, Skeleton, Statistic, Table, Typography } from 'antd';
import { ArrowRightOutlined, CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunSummary } from '../lib/types';
import { ExecutionLaunchDialog } from '../features/executions/ExecutionLaunchDialog';

const { Title, Text } = Typography;
function formatTime(value?: string | null) {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD HH:mm:ss') : value;
}
function priorityFor(status: RunSummary['status']) {
  if (status === 'waiting_approval') return { label: '需要决策', tone: 'priority-amber' };
  if (status === 'failed' || status === 'partial_failed') return { label: '需要排查', tone: 'priority-red' };
  if (status === 'running' || status === 'queued' || status === 'pause_requested') return { label: '正在执行', tone: 'priority-blue' };
  return { label: '已归档', tone: 'priority-neutral' };
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [launchOpen, setLaunchOpen] = useState(false);
  const query = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 });
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const data = query.data;
  const recentRuns = data?.recent_runs ?? [];
  const needsAttention = recentRuns.filter((run) => ['waiting_approval', 'failed', 'partial_failed'].includes(run.status));

  const chartOption = {
    animationDuration: 350,
    tooltip: { trigger: 'axis' },
    legend: { data: ['成功', '失败'], bottom: 0, itemWidth: 9, itemHeight: 9, textStyle: { color: '#7b8799', fontSize: 11 } },
    grid: { left: 8, right: 10, top: 20, bottom: 42, containLabel: true },
    xAxis: { type: 'category', data: data?.daily_trend.map((item) => item.date) ?? [], axisTick: { show: false }, axisLine: { lineStyle: { color: '#e6eaf1' } }, axisLabel: { color: '#8792a4', fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#8792a4', fontSize: 11 }, splitLine: { lineStyle: { color: '#edf0f5', type: 'dashed' } } },
    series: [
      { name: '成功', type: 'bar', stack: 'total', barMaxWidth: 22, data: data?.daily_trend.map((item) => item.succeeded) ?? [], itemStyle: { color: '#557be8', borderRadius: [3, 3, 0, 0] } },
      { name: '失败', type: 'bar', stack: 'total', barMaxWidth: 22, data: data?.daily_trend.map((item) => item.failed) ?? [], itemStyle: { color: '#e47a75', borderRadius: [3, 3, 0, 0] } },
    ],
  };

  const columns = [
    {
      title: '执行轮次', dataIndex: 'run_id', key: 'run_id',
      render: (value: string, row: RunSummary) => (
        <button className="table-identity" onClick={() => navigate('/runs/' + encodeURIComponent(value))}>
          <span className="table-identity-mark"><ClockCircleOutlined /></span>
          <span><strong>{value}</strong><small>{row.task_count} 个任务 · 发起人 {row.operator || '系统'}</small></span>
        </button>
      ),
    },
    { title: '当前状态', dataIndex: 'status', key: 'status', render: (value: RunSummary['status']) => <StatusTag status={value} /> },
    {
      title: '处理进度', key: 'progress',
      render: (_: unknown, row: RunSummary) => {
        const ratio = row.task_count > 0 ? Math.min(100, Math.round(row.succeeded_tasks / row.task_count * 100)) : 0;
        return <div className="run-progress-cell"><div><span>{row.succeeded_tasks}/{row.task_count} 已完成</span><span>{ratio}%</span></div><Progress percent={ratio} showInfo={false} size="small" /></div>;
      },
    },
    { title: '启动时间', dataIndex: 'started_at', key: 'started_at', render: formatTime },
    { title: '处理状态', key: 'attention', render: (_: unknown, row: RunSummary) => { const priority = priorityFor(row.status); return <span className={'priority-pill ' + priority.tone}>{priority.label}</span>; } },
    { title: '', key: 'action', render: (_: unknown, row: RunSummary) => <Button type="text" icon={<ArrowRightOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>查看</Button> },
  ];

  return (
    <div className="page-stack dashboard-page">
      <section className="overview-hero">
        <div className="overview-hero-copy">
          <span className="hero-kicker"><span className="hero-kicker-dot" /> 运维控制台 / 异常修复</span>
          <Title level={2}>异常分诊台</Title>
          <Text>优先处理需要决策或排查的执行轮次。每轮任务串行运行，证据、变更、验证和交付记录都可追溯。</Text>
        </div>
        <div className="overview-hero-actions">
          <Button icon={<ReloadOutlined />} onClick={() => { query.refetch(); health.refetch(); }} loading={query.isFetching}>刷新</Button>
          <Button type="primary" size="large" icon={<PlusOutlined />} disabled={Boolean(health.data && !health.data.source_repo_configured)} onClick={() => setLaunchOpen(true)}>开始异常扫描</Button>
        </div>
        <div className="hero-orbit hero-orbit-one" /><div className="hero-orbit hero-orbit-two" />
      </section>

      {health.data && !health.data.source_repo_configured && <Alert showIcon type="warning" message="源代码仓库尚未配置" description="服务可连接，但 AI_REPAIR_SOURCE_REPO 未配置。请先完成服务端配置，再启动扫描。" />}
      {health.isError && <Alert showIcon type="error" message="工作流服务连接异常" description={(health.error as Error).message} action={<Button type="link" onClick={() => health.refetch()}>重新连接</Button>} />}
      {query.isError && <Alert showIcon type="error" message="概览数据加载失败" description={(query.error as Error).message} action={<Button type="link" onClick={() => query.refetch()}>重试</Button>} />}

      <div className="section-heading">
        <div><Title level={4}>当前处理态势</Title><Text type="secondary">来自工作流服务的实时统计，不包含前端推测数据。</Text></div>
        <span className="refresh-caption"><i className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} /> 每 15 秒更新队列</span>
      </div>

      <div className="overview-metrics">
        <Card className="overview-metric metric-running" onClick={() => navigate('/runs?status=running')} hoverable>
          <div className="metric-label"><span className="metric-icon"><PlayCircleOutlined /></span><span>正在运行</span></div>
          <Statistic value={data?.active_runs ?? 0} loading={query.isLoading} />
          <div className="metric-foot"><span>串行执行中</span><ArrowRightOutlined /></div>
        </Card>
        <Card className="overview-metric metric-approval" onClick={() => navigate('/approvals')} hoverable>
          <div className="metric-label"><span className="metric-icon"><SafetyCertificateOutlined /></span><span>待最终审批</span></div>
          <Statistic value={approvals.data?.length ?? data?.waiting_approvals ?? 0} loading={approvals.isLoading || query.isLoading} />
          <div className="metric-foot"><span>交付后作出决定</span><ArrowRightOutlined /></div>
        </Card>
        <Card className="overview-metric metric-failed" onClick={() => navigate('/runs?status=failed')} hoverable>
          <div className="metric-label"><span className="metric-icon"><ExclamationCircleOutlined /></span><span>24 小时失败</span></div>
          <Statistic value={data?.failed_24h ?? 0} loading={query.isLoading} />
          <div className="metric-foot"><span>查看失败与部分失败</span><ArrowRightOutlined /></div>
        </Card>
        <Card className="overview-metric metric-success" onClick={() => navigate('/runs?status=succeeded')} hoverable>
          <div className="metric-label"><span className="metric-icon"><CheckCircleOutlined /></span><span>24 小时成功</span></div>
          <Statistic value={data?.succeeded_24h ?? 0} loading={query.isLoading} />
          <div className="metric-foot"><span>近 7 天成功率 {((data?.success_rate_7d ?? 0) * 100).toFixed(1)}%</span><ArrowRightOutlined /></div>
        </Card>
      </div>

      <Row gutter={[18, 18]} align="stretch">
        <Col xs={24} xl={16}>
          <Card className="content-card triage-card" title={<div className="card-title-group"><span>需要关注</span><Text type="secondary">从最近执行中识别的待处理项</Text></div>} extra={<Button type="link" onClick={() => navigate('/runs')}>所有执行 <ArrowRightOutlined /></Button>}>
            {query.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : needsAttention.length ? (
              <div className="triage-list">{needsAttention.slice(0, 5).map((run) => {
                const priority = priorityFor(run.status);
                return <button className="triage-item" key={run.run_id} onClick={() => navigate('/runs/' + encodeURIComponent(run.run_id))}>
                  <span className={'triage-status-icon ' + priority.tone}>{run.status === 'waiting_approval' ? <SafetyCertificateOutlined /> : <ExclamationCircleOutlined />}</span>
                  <span className="triage-item-main"><strong>{run.run_id}</strong><span>{run.status === 'waiting_approval' ? '执行已进入最终审批阶段' : run.status === 'partial_failed' ? '本轮存在未成功完成的任务' : '执行失败，需要检查终态报告与步骤记录'}</span></span>
                  <span className="triage-item-meta"><StatusTag status={run.status} /><small>{formatTime(run.started_at)}</small></span><ArrowRightOutlined className="triage-arrow" />
                </button>;
              })}</div>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="最近执行中没有待分诊项" />}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card className="content-card service-card" title={<div className="card-title-group"><span>服务与门禁</span><Text type="secondary">后端健康状态</Text></div>} extra={<span className={'service-state-tag ' + (health.isSuccess ? 'is-ready' : health.isError ? 'is-down' : '')}>{health.isSuccess ? '运行正常' : health.isError ? '连接异常' : '检查中'}</span>}>
            <div className="service-rows">
              <div className="service-row"><span>工作流 API</span><strong><i className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />{health.isSuccess ? '可连接' : health.isError ? '不可连接' : '检查中'}</strong></div>
              <div className="service-row"><span>源代码仓库</span><strong>{health.data ? (health.data.source_repo_configured ? '已配置' : '未配置') : '—'}</strong></div>
              <div className="service-row"><span>运行目录</span><strong>{health.data ? (health.data.runtime_dir_configured ? '已配置' : '未配置') : '—'}</strong></div>
              <div className="service-row"><span>最终审批队列</span><strong>{approvals.data?.length ?? '—'} 项</strong></div>
            </div>
            <div className="service-footnote">前端仅展示状态；任务资格、文件范围、权限和审批合法性始终由服务端校验。</div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[18, 18]} align="stretch">
        <Col xs={24} xl={15}>
          <Card className="content-card trend-card" title={<div className="card-title-group"><span>处理趋势</span><Text type="secondary">最近 7 天任务处理量</Text></div>} extra={<span className="trend-summary">成功率 <strong>{((data?.success_rate_7d ?? 0) * 100).toFixed(1)}%</strong></span>}>
            {query.isLoading ? <Skeleton active /> : data?.daily_trend.length ? <ReactECharts option={chartOption} style={{ height: 260 }} notMerge /> : <Empty description="暂无趋势数据" />}
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className="content-card process-card" title={<div className="card-title-group"><span>修复处理路径</span><Text type="secondary">关键控制点</Text></div>}>
            <div className="process-list">
              <div className="process-step"><span className="process-step-number">01</span><div><strong>证据与根因定位</strong><small>先确认日志事实和源码证据</small></div><span className="process-step-kind">只读</span></div>
              <div className="process-step"><span className="process-step-number">02</span><div><strong>受限范围内修复</strong><small>仅允许运输商目录内的修复变更</small></div><span className="process-step-kind">隔离区</span></div>
              <div className="process-step"><span className="process-step-number">03</span><div><strong>校验、回归与审查</strong><small>失败任务独立收敛，不阻塞其他任务</small></div><span className="process-step-kind">门禁</span></div>
              <div className="process-step"><span className="process-step-number">04</span><div><strong>交付后统一审批</strong><small>审批前不代表已经合并代码</small></div><span className="process-step-kind">人工</span></div>
            </div>
          </Card>
        </Col>
      </Row>

      <Card className="content-card recent-runs-card" title={<div className="card-title-group"><span>最近执行</span><Text type="secondary">点击任一轮次进入实时执行视图</Text></div>} extra={<Button type="link" onClick={() => navigate('/runs')}>打开执行记录 <ArrowRightOutlined /></Button>}>
        {query.isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : (
          <Table rowKey="run_id" columns={columns} dataSource={recentRuns} pagination={false} size="middle"
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有执行记录。可以创建一次异常扫描。" /> }} />
        )}
      </Card>
      <ExecutionLaunchDialog open={launchOpen} onClose={() => setLaunchOpen(false)} />
    </div>
  );
}
