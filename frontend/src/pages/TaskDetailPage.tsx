import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Empty, message, Modal, Progress, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import { StructuredValue } from '../components/StructuredValue';
import type { ArtifactRef, StepRecord, WorkflowReport } from '../lib/types';

const { Title, Text, Paragraph } = Typography;
function formatDuration(value?: number | null) {
  if (value == null || value < 0) return '—';
  return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms';
}
function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = dayjs(value);
  return date.isValid() ? date.format('YYYY-MM-DD HH:mm:ss') : value;
}
function pretty(value: unknown) {
  if (value == null) return '暂无数据';
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}
function resultText(value?: string | null) {
  if (!value) return '等待结果';
  const labels: Record<string, string> = {
    SUCCESS: '修复已完成', PASS: '审查通过', FAIL: '审查未通过', DELIVERED: '已交付',
    MANUAL: '需要人工判断', INSUFFICIENT_EVIDENCE: '证据不足', REJECTED: '已拒绝',
    FAILED: '执行失败', INCOMPLETE: '结果不完整', LOCATED: '根因已定位',
  };
  return labels[value.toUpperCase()] ?? value;
}

export function TaskDetailPage() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('steps');
  const [selectedStepName, setSelectedStepName] = useState<string>();
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactRef | null>(null);
  const [retryAwaiting, setRetryAwaiting] = useState(false);
  const queryClient = useQueryClient();

  const detail = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    refetchInterval: (query) => retryAwaiting || ['queued', 'running', 'waiting_approval', 'paused', 'pause_requested'].includes(query.state.data?.run_status ?? '') ? 3000 : false,
  });
  const payload = detail.data;
  const task = payload?.task;
  const report: WorkflowReport = task?.report ?? { result: task?.result };
  const steps = task?.steps ?? [];
  const activeRun = ['queued', 'running', 'waiting_approval', 'paused', 'pause_requested'].includes(payload?.run_status ?? '');

  useEffect(() => {
    if (!selectedStepName && steps.length) setSelectedStepName(steps[steps.length - 1].name);
    if (selectedStepName && !steps.some((step) => step.name === selectedStepName)) setSelectedStepName(steps.length ? steps[steps.length - 1].name : undefined);
  }, [steps, selectedStepName]);

  const calls = useQuery({
    queryKey: ['model-calls', runId, taskId],
    queryFn: () => api.modelCalls(runId, taskId),
    enabled: Boolean(payload),
    refetchInterval: activeRun ? 5000 : false,
  });
  const retryStep = useMutation({
    mutationFn: (stepName: string) => api.retryFailedStep(runId, taskId, stepName),
    onSuccess: async (result) => {
      setRetryAwaiting(true);
      message.success(result.message || '已提交失败节点重试');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task', runId, taskId] }),
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
      ]);
    },
    onError: (error) => message.error('节点重试失败：' + (error as Error).message),
  });
  useEffect(() => {
    if (!retryAwaiting) return;
    if (payload && payload.run_status !== 'failed') {
      setRetryAwaiting(false);
      return;
    }
    const timeout = window.setTimeout(() => setRetryAwaiting(false), 60_000);
    return () => window.clearTimeout(timeout);
  }, [retryAwaiting, payload?.run_status]);
  const artifactQuery = useQuery({
    queryKey: ['task-artifact', runId, taskId, selectedArtifact?.path],
    queryFn: () => api.artifactContent(runId, selectedArtifact!.path, taskId),
    enabled: Boolean(selectedArtifact),
  });
  if (detail.isLoading) return <div className="page-loading"><Spin size="large" /><Text type="secondary">正在加载任务诊断…</Text></div>;
  if (detail.isError || !payload || !task) return (
    <div className="page-stack">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>返回执行详情</Button>
      <Alert type="error" showIcon message="无法加载任务诊断" description={(detail.error as Error)?.message ?? '任务不存在或服务端未返回任务记录'} action={<Button onClick={() => detail.refetch()}>重试</Button>} />
    </div>
  );

  const result = String(report.result || task.result || 'INCOMPLETE');
  const manual = ['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result.toUpperCase());
  const failed = ['FAILED', 'FAIL', 'REJECTED'].includes(result.toUpperCase());
  const selectedStep = steps.find((step) => step.name === selectedStepName);
  const artifacts = payload.artifacts ?? [];
  const problemRootCause = String((task.problem as Record<string, unknown> | undefined)?.root_cause || '暂无根因结论');
  const completedSteps = steps.filter((step) => ['succeeded', 'skipped'].includes(step.status)).length;
  const progress = steps.length ? Math.round(completedSteps / steps.length * 100) : 0;

  const stepColumns = [
    { title: '执行步骤', dataIndex: 'title', key: 'title', render: (title: string, row: StepRecord) => <div className="step-name-cell"><strong>{title || row.name}</strong><code>{row.name}</code></div> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: StepRecord['status']) => <StatusTag status={value} /> },
    { title: '业务结论', dataIndex: 'result', key: 'result', render: (value?: string | null) => value ? <Tag>{resultText(value)}</Tag> : '—' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: formatDuration },
    { title: '尝试', dataIndex: 'attempt', key: 'attempt', render: (value?: number) => value ?? 1 },
    { title: '操作', key: 'inspect', render: (_: unknown, row: StepRecord) => <Space size="small"><Button type="link" onClick={() => setSelectedStepName(row.name)}>查看详情</Button>{payload.retryable_steps?.includes(row.name) && <Button type="link" danger loading={retryStep.isPending && retryStep.variables === row.name} disabled={retryStep.isPending} onClick={() => Modal.confirm({ title: '重试失败节点？', content: '系统会再次验证该节点仍是 LangGraph checkpoint 中的待执行节点。成功后按原工作流路由继续；不会重跑整个 Run。', okText: '确认重试', cancelText: '取消', onOk: () => retryStep.mutateAsync(row.name) })}>重试节点</Button>}</Space> },
  ];
  const callColumns = [
    { title: 'Agent 角色', dataIndex: 'agent_role', key: 'agent_role', render: (value: string) => <strong>{value}</strong> },
    { title: '模型', dataIndex: 'model', key: 'model', render: (value?: string) => value || '—' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: string) => <StatusTag status={value} /> },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: formatDuration },
    { title: 'Token', dataIndex: 'total_tokens', key: 'total_tokens', render: (value?: number | null) => value ?? '—' },
    { title: '公开输出摘要', dataIndex: 'output_preview', key: 'output_preview', render: (value?: string) => <span className="cell-preview" title={value}>{value || '未采集'}</span> },
  ];
  const artifactColumns = [
    { title: '产物', dataIndex: 'name', key: 'name', render: (value: string, row: ArtifactRef) => <button className="artifact-open" onClick={() => setSelectedArtifact(row)}><FileTextOutlined /><span><strong>{value || row.path}</strong><small>{row.description || row.path}</small></span></button> },
    { title: '归属任务', dataIndex: 'task_id', key: 'task_id', render: (value?: string) => value || taskId },
    { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', render: (value?: number) => value == null ? '—' : value < 1024 ? value + ' B' : (value / 1024).toFixed(1) + ' KB' },
    { title: '查看', key: 'action', render: (_: unknown, row: ArtifactRef) => <Button type="link" onClick={() => setSelectedArtifact(row)}>预览内容</Button> },
  ];

  return (
    <div className="page-stack task-detail-page">
      <div className="page-heading task-page-heading">
        <div className="task-heading-copy">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>返回执行详情</Button>
          <div className="task-title-line"><Title level={3}>{task.carrier || '未知运输商'} <span>/</span> {task.biztype || '未知业务类型'}</Title><StatusTag status={result} /></div>
          <Text type="secondary" className="run-id">Run {runId} <span>·</span> Task {task.task_id}</Text>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => detail.refetch()} loading={detail.isFetching}>刷新诊断</Button>
        </Space>
      </div>

      {report.legacy_report && <Alert type="warning" showIcon message="这是历史任务报告" description="当前记录未包含完整的终态报告，页面仅展示服务端现存的步骤与结果，不推断缺失结论。" />}

      <section className={'task-outcome-banner ' + (manual ? 'is-manual' : failed ? 'is-failed' : 'is-complete')}>
        <div className="outcome-symbol">{manual ? '!' : failed ? '×' : '✓'}</div>
        <div className="outcome-copy"><span>最终结论</span><strong>{resultText(result)}</strong><p>{String(report.terminal_reason || task.terminal_reason || report.root_cause || '服务端未提供额外结论。')}</p></div>
        <div className="outcome-meta">
          <div><span>结束步骤</span><strong>{report.terminal_step || task.terminal_step || '—'}</strong></div>
          <div><span>总耗时</span><strong>{formatDuration(report.elapsed_ms ?? task.duration_ms)}</strong></div>
          <div><span>Token 消耗</span><strong>{report.tokens ?? '—'}</strong></div>
        </div>
      </section>

      <div className="task-overview-grid">
        <Card className="content-card task-summary-card" title="根因与处理摘要">
          <div className="summary-field"><span>问题摘要</span>{typeof report.problem === 'string' ? <Paragraph>{report.problem}</Paragraph> : <StructuredValue value={report.problem ?? task.problem ?? '没有可用的问题摘要'} />}</div>
          <div className="summary-field"><span>根因判断</span><Paragraph>{report.root_cause || problemRootCause}</Paragraph></div>
          <div className="summary-field"><span>变更摘要</span><Paragraph>{report.change_summary || '暂无变更摘要'}</Paragraph></div>
          {report.next_action && <div className="next-action-box"><strong>下一步建议</strong><span>{report.next_action}</span></div>}
        </Card>
        <Card className="content-card task-facts-card" title="任务信息">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="最终状态"><StatusTag status={result} /></Descriptions.Item>
            <Descriptions.Item label="工作流状态"><StatusTag status={payload.run_status} /></Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatDate(steps[0]?.started_at)}</Descriptions.Item>
            <Descriptions.Item label="结束时间">{formatDate(report.finished_at)}</Descriptions.Item>
            <Descriptions.Item label="执行步骤">{steps.length} 个</Descriptions.Item>
            <Descriptions.Item label="产物文件">{artifacts.length} 个</Descriptions.Item>
          </Descriptions>
          <div className="task-progress-caption"><span>节点完成度</span><strong>{progress}%</strong></div>
          <Progress percent={progress} showInfo={false} size="small" />
        </Card>
      </div>

      <Card className="content-card task-inspection-card" title={<div className="card-title-group"><span>诊断检查器</span><Text type="secondary">节点审计、模型调用、受控产物和终态报告</Text></div>}>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          { key: 'steps', label: '节点执行', children: (
            <div className="task-steps-layout">
              <div className="task-steps-table">
                <Table rowKey="name" columns={stepColumns} dataSource={steps} pagination={false} size="small" scroll={{ x: 760 }}
                  onRow={(row) => ({ onClick: () => setSelectedStepName(row.name), className: selectedStepName === row.name ? 'selected-task-row' : 'clickable-row' })}
                  locale={{ emptyText: <Empty description="服务端没有步骤记录" /> }} />
              </div>
              <div className="step-inspector">
                <div className="inspector-heading"><div><span>选中步骤</span><strong>{selectedStep?.title || selectedStep?.name || '尚未选择'}</strong></div>{selectedStep && <StatusTag status={selectedStep.status} />}</div>
                {selectedStep ? <>
                  <div className="inspector-facts"><span>步骤 ID<strong>{selectedStep.name}</strong></span><span>尝试次数<strong>{selectedStep.attempt ?? 1}</strong></span><span>耗时<strong>{formatDuration(selectedStep.duration_ms)}</strong></span></div>
                  <div className="inspector-summary">{selectedStep.summary || selectedStep.error?.message || '没有额外摘要。'}</div>
                  {selectedStep.error?.message && <Alert type="error" showIcon message={selectedStep.error.code || '步骤错误'} description={selectedStep.error.message} />}
                  <div className="json-panel-wrap"><div className="inspector-label">节点输入 · 字段视图</div><StructuredValue value={selectedStep.input} /></div>
                  <div className="json-panel-wrap"><div className="inspector-label">节点输出 · 字段视图</div><StructuredValue value={selectedStep.output} /></div>
                </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个节点查看执行证据" />}
              </div>
            </div>
          )},
          { key: 'models', label: '模型调用', children: (
            <div className="inspection-tab-content">
              <div className="inspection-tab-intro"><div><strong>模型调用审计</strong><span>展示可公开的调用元信息和输出摘要，不展示隐藏推理链。</span></div><Tag>{calls.data?.total ?? 0} 次调用</Tag></div>
              {calls.isError ? <Alert type="error" showIcon message="模型调用记录加载失败" description={(calls.error as Error).message} /> : <Table rowKey="model_call_id" columns={callColumns} dataSource={calls.data?.items ?? []} loading={calls.isLoading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} expandable={{ expandedRowRender: (call) => <div className="model-session-detail"><div className="inspection-tab-intro"><div><strong>会话事件</strong><span>按实际持久化顺序查看 Agent 文本、工具调用与工具结果。</span></div><Tag>{call.events?.length ?? 0} 条事件</Tag></div><Descriptions size="small" bordered column={{ xs: 1, sm: 2 }}><Descriptions.Item label="会话 ID">{call.model_call_id}</Descriptions.Item><Descriptions.Item label="尝试次数">{call.attempt}</Descriptions.Item><Descriptions.Item label="开始时间">{formatDate(call.started_at)}</Descriptions.Item><Descriptions.Item label="结束时间">{formatDate(call.finished_at)}</Descriptions.Item><Descriptions.Item label="事件持久化">{call.event_persistence_status || '未标记'}</Descriptions.Item><Descriptions.Item label="工具调用数">{call.tool_call_count ?? '—'}</Descriptions.Item></Descriptions>{call.error_type && <Alert type="error" showIcon message="调用失败" description={call.error_type} />}{call.events?.length ? <Table size="small" rowKey={(_, index) => String(index)} pagination={{ pageSize: 5, size: 'small', hideOnSinglePage: true }} dataSource={call.events} columns={[{ title: '序号', key: 'index', render: (_: unknown, __: unknown, index: number) => index + 1 }, { title: '类型', dataIndex: 'type', key: 'type', render: (value: string) => <Tag>{({ assistant: 'Agent 输出', tool_use: '工具调用', tool_result: '工具结果' } as Record<string, string>)[value] || value}</Tag> }, { title: '工具', dataIndex: 'tool', key: 'tool', render: (value?: string) => value || '—' }, { title: '耗时', dataIndex: 'elapsed_ms', key: 'elapsed_ms', render: formatDuration }, { title: '状态', dataIndex: 'is_error', key: 'is_error', render: (value?: boolean) => value ? <Tag color="red">错误</Tag> : <Tag>正常</Tag> }, { title: '内容 / 输入', key: 'payload', render: (_: unknown, event: { text?: string; input?: Record<string, unknown> }) => <StructuredValue value={event.text ?? event.input ?? {}} /> }]} /> : <Empty description={call.event_persistence_status === 'failed' ? '事件日志持久化失败，无法还原会话' : '该模型调用没有可用的会话事件'} />}{call.output_preview && <><div className="inspector-label">调用输出摘要</div><StructuredValue value={call.output_preview} /></>}</div> }} locale={{ emptyText: <Empty description="此任务没有模型调用记录" /> }} />}
            </div>
          )},
          { key: 'artifacts', label: '产物与报告', children: (
            <div className="inspection-tab-content">
              <div className="inspection-tab-intro"><div><strong>受控产物</strong><span>只通过 API 获取授权产物内容，不暴露本地磁盘路径。</span></div><Tag>{artifacts.length} 个文件</Tag></div>
              <Table rowKey={(row) => (row.task_id || taskId) + ':' + row.path} columns={artifactColumns} dataSource={artifacts} pagination={false} scroll={{ x: 780 }} locale={{ emptyText: <Empty description="当前任务没有可展示的产物" /> }} />
              {selectedArtifact && <div className="artifact-preview-panel">
                <div className="inspector-heading"><div><span>产物预览</span><strong>{selectedArtifact.name || selectedArtifact.path}</strong></div><Button type="text" onClick={() => setSelectedArtifact(null)}>关闭</Button></div>
                {artifactQuery.isLoading ? <Spin /> : artifactQuery.isError ? <Alert type="error" showIcon message="产物加载失败" description={(artifactQuery.error as Error).message} /> : <pre className="json-panel">{pretty(artifactQuery.data?.content)}</pre>}
              </div>}
            </div>
          )},
          { key: 'report', label: '终态报告', children: <div className="report-json"><StructuredValue value={report} /></div> },
        ]} />
      </Card>
    </div>
  );
}
