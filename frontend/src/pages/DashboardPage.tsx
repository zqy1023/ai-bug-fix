import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Progress, Skeleton, Statistic, Table, Typography } from 'antd';
import { ArrowRightOutlined, CheckCircleFilled, ClockCircleOutlined, ExclamationCircleFilled, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunSummary, StepRecord } from '../lib/types';
import { ExecutionLaunchDialog } from '../features/executions/ExecutionLaunchDialog';

const { Title, Text } = Typography;
const statusLabel: Record<string, string> = {
  running: '运行中', queued: '排队中', waiting: '等待中', waiting_approval: '待审批',
  failed: '失败', partial_failed: '部分失败', succeeded: '已完成', cancelled: '已取消',
  paused: '已暂停', pause_requested: '暂停中', cancel_requested: '停止中',
};
function time(value?: string | null) {
  if (!value) return '—';
  const d = dayjs(value);
  return d.isValid() ? d.format('HH:mm:ss') : value;
}
function duration(start?: string | null, finish?: string | null) {
  if (!start) return '—';
  const seconds = Math.max(0, dayjs(finish || undefined).diff(dayjs(start), 'second'));
  if (!finish) return seconds >= 3600 ? Math.floor(seconds / 3600) + ' 小时' : Math.max(1, Math.floor(seconds / 60)) + ' 分钟';
  if (seconds >= 3600) return Math.floor(seconds / 3600) + ' 小时 ' + Math.floor(seconds % 3600 / 60) + ' 分钟';
  return Math.max(1, Math.floor(seconds / 60)) + ' 分钟';
}
function summarize(step: StepRecord) {
  if (step.error?.message) return step.error.message;
  if (step.summary) return step.summary;
  if (step.result) return String(step.result);
  return step.status === 'succeeded' ? '步骤已完成' : step.status === 'running' ? '正在处理' : step.status === 'failed' ? '步骤失败' : '等待执行';
}
const workflowLabels: Record<string, string> = {
  discover_logs: '发现异常', normalize_error: '规范化与去重', deduplicate_error: '规范化与去重',
  identify_carrier: '匹配运输商', prepare_source_snapshot: '准备源码快照', prepare_evidence: '整理证据',
  locate_agent: '根因定位', claim_repair_slot: '修复资格门禁', prepare_workspace: '准备隔离工作区',
  repair_agent: '修复代码', check_changes: '变更范围检查', verify_issue: '问题验证',
  run_regression: '回归验证', review_agent: '代码审查', request_approval: '最终审批',
  apply_plan: '应用交付方案', prepare_delivery: '准备交付', finalize_execution: '完成与报告',
};
function priority(run: RunSummary) {
  if (run.status === 'waiting_approval') return { label: '待审批', tone: 'queue-amber' };
  if (run.status === 'failed' || run.status === 'partial_failed') return { label: '失败', tone: 'queue-red' };
  if (run.status === 'running' || run.status === 'queued' || run.status === 'pause_requested') return { label: '运行中', tone: 'queue-green' };
  if (run.manual_tasks > 0) return { label: '需判断', tone: 'queue-blue' };
  return { label: statusLabel[run.status] || run.status, tone: 'queue-muted' };
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [launchOpen, setLaunchOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState('');
  const summary = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000 });
  const runsQuery = useQuery({ queryKey: ['dashboard-runs'], queryFn: () => api.runs({ page: 1, page_size: 12 }), refetchInterval: 15000 });
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000 });
  const recentRuns = runsQuery.data?.items ?? summary.data?.recent_runs ?? [];
  const selectedId = selectedRunId || recentRuns[0]?.run_id || '';
  const selectedRun = useQuery({ queryKey: ['dashboard-selected-run', selectedId], queryFn: () => api.run(selectedId), enabled: Boolean(selectedId), refetchInterval: 10000 });
  const run = selectedRun.data;
  const steps = run?.steps ?? [];
  const task = run?.tasks?.[0];
  const activeStep = steps.find((step) => step.status === 'running' || step.status === 'waiting') ?? [...steps].reverse().find((step) => step.status === 'failed' || step.status === 'succeeded');
  const attentionRuns = useMemo(() => recentRuns.filter((item) =>
    ['waiting_approval', 'failed', 'partial_failed'].includes(item.status) || item.manual_tasks > 0
  ), [recentRuns]);
  const finishedToday = (summary.data?.succeeded_24h ?? 0) + (summary.data?.failed_24h ?? 0);
  const columns = [
    { title: '任务 / 执行轮次', key: 'identity', render: (_: unknown, row: RunSummary) => <button className="sketch-identity" onClick={() => setSelectedRunId(row.run_id)}><span className={'carrier-monogram carrier-' + (row.status === 'failed' ? 'red' : row.status === 'waiting_approval' ? 'purple' : 'blue')}>{(row.operator || 'R').slice(0, 1).toUpperCase()}</span><span className="sketch-identity-copy"><strong>{row.run_id}</strong><small>{row.task_count} 个任务 · {row.failed_tasks ? row.failed_tasks + ' 个失败' : row.manual_tasks ? row.manual_tasks + ' 个需判断' : '工作流自动执行'}</small></span></button> },
    { title: '当前阶段', key: 'stage', render: (_: unknown, row: RunSummary) => row.status === 'waiting_approval' ? '最终审批' : row.status === 'failed' || row.status === 'partial_failed' ? '异常收敛' : row.status === 'succeeded' ? '已完成' : '工作流处理中' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: RunSummary['status']) => <StatusTag status={value} /> },
    { title: '已耗时', key: 'duration', render: (_: unknown, row: RunSummary) => duration(row.started_at, row.finished_at) },
    { title: '更新时间', dataIndex: 'started_at', key: 'updated', render: time },
    { title: '', key: 'open', render: (_: unknown, row: RunSummary) => <Button type="text" icon={<ArrowRightOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))} aria-label="打开运行详情" /> },
  ];

  return <div className="page-stack sketch-dashboard">
    <div className="sketch-page-heading">
      <div><Title level={2}>运行工作台</Title><Text>从异常发现到交付审批，集中处理每一条修复任务。</Text></div>
      <div className="sketch-heading-actions"><span>最近更新 {time(summary.data?.recent_runs?.[0]?.started_at)}</span><Button icon={<ReloadOutlined />} onClick={() => { void summary.refetch(); void runsQuery.refetch(); void selectedRun.refetch(); }} loading={summary.isFetching || runsQuery.isFetching}>刷新</Button><Button type="primary" icon={<PlusOutlined />} disabled={Boolean(health.data && !health.data.source_repo_configured)} onClick={() => setLaunchOpen(true)}>新建扫描</Button></div>
    </div>
    {health.isError && <Alert showIcon type="error" message="工作流服务连接失败" description={(health.error as Error).message} action={<Button type="link" onClick={() => health.refetch()}>重试</Button>} />}
    {health.data && !health.data.source_repo_configured && <Alert showIcon type="warning" message="源代码仓库尚未配置" description="服务已连接，但尚未配置可用于修复的源仓库。启动扫描前请先完成后端配置。" />}
    {summary.isError && <Alert showIcon type="error" message="运行概览加载失败" description={(summary.error as Error).message} />}
    <section className="sketch-metrics">
      <Card className="sketch-metric"><div className="sketch-metric-top"><span className="metric-dot blue" />正在运行</div><Statistic value={summary.data?.active_runs ?? 0} loading={summary.isLoading} /><div className="sketch-metric-foot">当前活动执行轮次</div><span className="metric-spark spark-blue">⌁⌁⌁⌁</span></Card>
      <Card className="sketch-metric"><div className="sketch-metric-top"><span className="metric-dot amber" />待最终审批</div><Statistic value={approvals.data?.length ?? summary.data?.waiting_approvals ?? 0} loading={approvals.isLoading || summary.isLoading} /><div className="sketch-metric-foot">等待人工交付决策</div><span className="metric-spark spark-amber">⌁⌁⌁⌁</span></Card>
      <Card className="sketch-metric"><div className="sketch-metric-top"><span className="metric-dot red" />需要关注</div><Statistic value={attentionRuns.length} loading={runsQuery.isLoading} /><div className="sketch-metric-foot">失败、部分失败或需判断</div><span className="metric-spark spark-red">⌁⌁⌁⌁</span></Card>
      <Card className="sketch-metric"><div className="sketch-metric-top"><span className="metric-dot green" />今日已完成</div><Statistic value={finishedToday} loading={summary.isLoading} /><div className="sketch-metric-foot">近 24 小时成功与失败终态</div><span className="metric-spark spark-green">⌁⌁⌁⌁</span></Card>
    </section>
    <section className="sketch-main-grid">
      <Card className="sketch-queue-card" title={<div className="sketch-card-title"><strong>任务队列</strong><span>真实运行记录 · 选择一轮查看执行链路</span></div>} extra={<Button type="link" onClick={() => navigate('/runs')}>全部执行 <ArrowRightOutlined /></Button>}>
        <div className="sketch-queue-filters"><Button type="primary" size="small">全部 <span>{recentRuns.length}</span></Button><Button size="small" onClick={() => navigate('/runs?status=running')}>运行中</Button><Button size="small" onClick={() => navigate('/approvals')}>待审批 <span>{approvals.data?.length ?? 0}</span></Button><Button size="small" onClick={() => navigate('/runs?status=failed')}>失败</Button><span className="sketch-filter-spacer" /><Text type="secondary">自动刷新 · 15 秒</Text></div>
        {runsQuery.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : <Table rowKey="run_id" columns={columns} dataSource={recentRuns} pagination={false} size="small" onRow={(row) => ({ onClick: () => setSelectedRunId(row.run_id), className: row.run_id === selectedId ? 'sketch-selected-row' : '' })} locale={{ emptyText: <Empty description="暂无运行记录。可通过新建扫描启动工作流。" /> }} />}
      </Card>
      <div className="sketch-right-rail">
        <Card className="sketch-pipeline-card" title={<div className="sketch-card-title"><strong>执行链路</strong><span>{selectedId || '选择运行轮次'}</span></div>} extra={selectedId ? <Button type="text" onClick={() => navigate('/runs/' + encodeURIComponent(selectedId))}>详情</Button> : null}>
          {selectedRun.isLoading ? <Skeleton active paragraph={{ rows: 6 }} /> : !selectedId ? <Empty description="暂无执行链路" /> : <div className="sketch-pipeline">
            {steps.length ? steps.slice(0, 12).map((step: StepRecord, index: number) => <div className={'sketch-pipeline-step step-' + step.status} key={step.name}><span className="pipeline-marker">{step.status === 'succeeded' ? <CheckCircleFilled /> : step.status === 'failed' ? <ExclamationCircleFilled /> : index + 1}</span><div className="pipeline-step-copy"><strong>{workflowLabels[step.name] || step.title || step.name}</strong><small>{summarize(step)}</small></div><span className="pipeline-step-time">{step.duration_ms ? Math.round(step.duration_ms / 1000) + ' 秒' : statusLabel[step.status] || step.status}</span></div>) : <Empty description="当前轮次暂无步骤记录" />}
          </div>}
          {selectedRun.isError && <Alert type="warning" showIcon message="无法加载所选执行详情" description={(selectedRun.error as Error).message} />}
        </Card>
        <Card className="sketch-decision-card" title={<div className="sketch-card-title"><strong>需要你的决策</strong><span>最终审批与人工判断</span></div>} extra={<Button type="link" onClick={() => navigate('/approvals')}>查看</Button>}>
          {(approvals.data ?? []).length ? (approvals.data ?? []).slice(0, 3).map((item) => <button className="sketch-decision-item" key={item.approval_id} onClick={() => navigate('/approvals')}><span className="decision-badge"><SafetyCertificateOutlined /></span><span><strong>{item.carrier} / {item.biztype}</strong><small>{item.summary}</small><em>{dayjs(item.created_at).isValid() ? dayjs(item.created_at).format('MM-DD HH:mm') : time(item.created_at)}</em></span><ArrowRightOutlined /></button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审批事项" />}
        </Card>
      </div>
    </section>
    <Card className="sketch-evidence-card" title={<div className="sketch-card-title"><strong>证据摘要</strong><span>当前选中轮次的实时信息</span></div>} extra={selectedId ? <Button type="link" onClick={() => navigate('/runs/' + encodeURIComponent(selectedId))}>打开完整执行详情 <ArrowRightOutlined /></Button> : null}>
      {run ? <div className="sketch-evidence-layout"><div className="sketch-evidence-main"><div className="sketch-evidence-callout"><ExclamationCircleFilled /><span>{run.run_error?.message || task?.terminal_reason || activeStep?.error?.message || activeStep?.summary || '当前轮次没有可展示的异常摘要。详细诊断以任务报告和节点证据为准。'}</span></div><strong>关键事实</strong><div className="sketch-evidence-chips"><span>运行状态：{statusLabel[run.status] || run.status}</span><span>任务数量：{run.task_count}</span><span>失败任务：{run.failed_tasks}</span><span>需判断任务：{run.manual_tasks}</span></div></div><div className="sketch-evidence-facts"><div><span>执行轮次</span><strong>{run.run_id}</strong></div><div><span>当前步骤</span><strong>{activeStep ? workflowLabels[activeStep.name] || activeStep.title || activeStep.name : '—'}</strong></div><div><span>启动时间</span><strong>{time(run.started_at)}</strong></div><Button onClick={() => navigate('/runs/' + encodeURIComponent(run.run_id))}>打开完整执行详情 <ArrowRightOutlined /></Button></div></div> : <Empty description="选择一条运行记录以查看其证据摘要" />}
    </Card>
    <ExecutionLaunchDialog open={launchOpen} onClose={() => setLaunchOpen(false)} />
  </div>;
}
