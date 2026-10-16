import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Descriptions, Drawer, Empty, Space, Spin, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import { WorkflowGraph } from '../components/WorkflowGraph';
import type { ModelCall, StepRecord } from '../lib/types';

const { Title, Text, Paragraph } = Typography;
const active = ['running', 'queued', 'waiting_approval', 'pause_requested', 'paused', 'cancel_requested'];
function duration(value?: number | null) {
  if (value == null) return '—';
  return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms';
}
function resultLabel(value?: string | null) {
  if (!value) return '暂无结论';
  const labels: Record<string, string> = {
    SUCCESS: '流程完成', SUCCEEDED: '已完成', MANUAL: '需人工介入',
    INSUFFICIENT_EVIDENCE: '证据不足', FAILED: '失败', FAIL: '验证未通过',
    REJECTED: '人工拒绝', CANCELLED: '已取消', INCOMPLETE: '未完整结束',
    RUNNING: '执行中', PENDING: '等待执行', WAITING_APPROVAL: '等待审批',
    PASS: '审查通过', LOCATED: '已定位根因', DELIVERED: '已准备交付',
  };
  return labels[value.toUpperCase()] ?? value;
}

export function TaskDetailPage() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const [selectedStepName, setSelectedStepName] = useState<string>();
  const [artifactPath, setArtifactPath] = useState<{ path: string; name: string } | null>(null);
  const detail = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    refetchInterval: (q) => active.includes(q.state.data?.run_status ?? '') ? 3000 : false,
  });
  const taskResponse = detail.data;
  const task = taskResponse?.task;
  const report = task?.report;
  const steps = task?.steps ?? [];
  const captureKnowledge = useMutation({
    mutationFn: () => api.captureKnowledgeFromTask(runId, taskId),
    onSuccess: async () => {
      message.success('已从当前任务记录沉淀候选知识');
      await queryClient.invalidateQueries({ queryKey: ['knowledge-cases'] });
    },
    onError: (error) => message.error('沉淀知识失败：' + (error as Error).message),
  });
  const calls = useQuery({
    queryKey: ['model-calls', runId, taskId],
    queryFn: () => api.modelCalls(runId, taskId),
    refetchInterval: active.includes(detail.data?.run_status ?? '') ? 5000 : false,
  });
  const artifact = useQuery({
    queryKey: ['task-artifact', runId, taskId, artifactPath?.path],
    queryFn: () => api.artifactContent(runId, artifactPath!.path, taskId),
    enabled: Boolean(artifactPath),
  });
  if (detail.isLoading) return <div className="page-loading"><Spin size="large" /><Text type="secondary">正在加载任务详情…</Text></div>;
  if (detail.isError || !taskResponse || !task || !report) return <div className="page-stack">
    <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>返回 Run 概览</Button>
    <Alert type="error" showIcon message="无法加载任务详情" description={(detail.error as Error)?.message ?? '任务不存在或终态报告不可用'} action={<Button onClick={() => detail.refetch()}>重试</Button>} />
  </div>;

  const result = String(report.result || task.result || 'INCOMPLETE');
  const manual = ['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result.toUpperCase());
  const step = steps.find((item) => item.name === selectedStepName);
  const problem = task.problem?.error;
  const stepColumns = [
    { title: '步骤', dataIndex: 'title', key: 'title', render: (v: string, row: StepRecord) => <div><strong>{v || row.name}</strong><div className="secondary-small">{row.name}</div></div> },
    { title: '执行状态', dataIndex: 'status', key: 'status', render: (v: StepRecord['status']) => <StatusTag status={v} /> },
    { title: '业务结论', dataIndex: 'result', key: 'result', render: (v?: string | null) => v ? <Tag>{resultLabel(v)}</Tag> : '—' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: duration },
    { title: '摘要 / 错误', key: 'summary', render: (_: unknown, row: StepRecord) => <div>{row.summary || '—'}{row.error?.message && <div className="text-danger">{row.error.message}</div>}</div> },
    { title: '操作', key: 'action', render: (_: unknown, row: StepRecord) => <Button type="link" onClick={() => setSelectedStepName(row.name)}>节点详情</Button> },
  ];
  const callColumns = [
    { title: 'Agent', dataIndex: 'agent_role', key: 'agent_role' },
    { title: '模型', dataIndex: 'model', key: 'model', render: (v?: string) => v || '—' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => <Tag>{resultLabel(v)}</Tag> },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: duration },
    { title: 'Token', dataIndex: 'total_tokens', key: 'total_tokens', render: (v?: number | null) => v ?? '—' },
    { title: '输出摘要', dataIndex: 'output_preview', key: 'output_preview', render: (v?: string) => <Text ellipsis={{ tooltip: v }} style={{ maxWidth: 360, display: 'inline-block' }}>{v || '未采集'}</Text> },
  ];
  return <div className="page-stack task-detail-page">
    <div className="page-heading run-heading">
      <div><Space wrap><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>返回 Run 概览</Button>
        <Title level={3} className="run-detail-title">{task.carrier || '未知运输商'} · {task.biztype || '未知业务类型'}</Title>
        <StatusTag status={result} /></Space>
        <Text type="secondary" className="run-id">Run {runId} · Task {task.task_id}</Text>
      </div>
      <Space>
        <Button icon={<ReloadOutlined />} onClick={() => detail.refetch()} loading={detail.isFetching}>刷新任务</Button>
        <Button disabled={active.includes(taskResponse.run_status) || captureKnowledge.isPending}
          loading={captureKnowledge.isPending} onClick={() => captureKnowledge.mutate()}>沉淀知识案例</Button>
      </Space>
    </div>
    {report.legacy_report && <Alert type="warning" showIcon message="历史任务没有完整的终态报告" description="当前结束原因根据现有步骤推导；重新执行后会写入完整终态报告。" />}
    <Card className="content-card" title="最终结论">
      <Space wrap style={{ marginBottom: 12 }}><Tag color={manual ? 'orange' : result.toUpperCase() === 'SUCCESS' ? 'green' : 'default'}>{resultLabel(result)}</Tag>
        {report.finished_at && <Text type="secondary">{dayjs(report.finished_at).isValid() ? dayjs(report.finished_at).format('YYYY-MM-DD HH:mm:ss') : report.finished_at}</Text>}</Space>
      <Descriptions bordered size="small" column={{ xs: 1, sm: 2, xl: 3 }}>
        <Descriptions.Item label="结束步骤">{report.terminal_step || '尚无记录'}</Descriptions.Item>
        <Descriptions.Item label="总耗时">{duration(report.elapsed_ms)}</Descriptions.Item>
        <Descriptions.Item label="Token 消耗">{report.tokens ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="结束原因" span={3}><Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{report.terminal_reason || '任务尚未结束，或历史记录未保存原因。'}</Paragraph></Descriptions.Item>
        <Descriptions.Item label="建议下一步" span={3}><Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{report.next_action || '继续观察执行状态。'}</Paragraph></Descriptions.Item>
      </Descriptions>
    </Card>
    <Card className="content-card" title="问题与修复摘要">
      <Descriptions bordered size="small" column={1}>
        {problem != null && <Descriptions.Item label="原始异常">{typeof problem === 'string' ? problem : <pre className="json-panel">{JSON.stringify(task.problem, null, 2)}</pre>}</Descriptions.Item>}
        <Descriptions.Item label="根因结论">{report.root_cause || '尚未形成根因结论；检查定位步骤及其证据。'}</Descriptions.Item>
        <Descriptions.Item label="变更摘要">{report.change_summary || '没有记录修复摘要，可能在定位或证据阶段提前终止。'}</Descriptions.Item>
      </Descriptions>
    </Card>
    <Card className="content-card" title={'任务执行图 · ' + task.carrier + ' / ' + task.biztype}><WorkflowGraph steps={steps} selectedStep={selectedStepName} onSelectStep={setSelectedStepName} /></Card>
    <Card className="content-card" title={'当前任务步骤记录（' + steps.length + '）'}>
      <Table rowKey="name" columns={stepColumns} dataSource={steps} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: '当前任务尚无步骤记录' }} />
    </Card>
    <Card className="content-card" title={'当前任务产物（' + taskResponse.artifacts.length + '）'}>
      <Table rowKey={(row) => row.task_id + ':' + row.path} dataSource={taskResponse.artifacts} pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: '该任务没有登记产物' }}
        columns={[
          { title: '产物', dataIndex: 'name', key: 'name' }, { title: '所属步骤', dataIndex: 'description', key: 'description' },
          { title: '类型', dataIndex: 'media_type', key: 'media_type' },
          { title: '操作', key: 'action', render: (_: unknown, row: { path: string; name: string }) => <Button type="link" onClick={() => setArtifactPath({ path: row.path, name: row.name })}>查看产物</Button> },
        ]} />
    </Card>
    <Card className="content-card" title={'当前任务 Agent 调用（' + (calls.data?.total ?? 0) + '）'}>
      <Text type="secondary">只加载当前 task_id 的模型调用记录，不混入同一 Run 的其他任务。</Text>
      {calls.isError && <Alert type="error" showIcon message="Agent 调用记录加载失败" description={(calls.error as Error).message} />}
      <Table rowKey="model_call_id" loading={calls.isLoading} dataSource={calls.data?.items ?? []} columns={callColumns}
        pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: '当前任务没有已记录的模型调用' }}
        expandable={{ expandedRowRender: (row: ModelCall) => <div className="page-stack">
          <Descriptions size="small" bordered column={2}><Descriptions.Item label="Provider">{row.provider || '—'}</Descriptions.Item>
            <Descriptions.Item label="尝试次数">{row.attempt ?? 1}</Descriptions.Item><Descriptions.Item label="Prompt 版本">{row.prompt_version || '—'}</Descriptions.Item>
            <Descriptions.Item label="事件持久化">{row.event_persistence_status || '未知'}</Descriptions.Item></Descriptions>
          {(row.events ?? []).length ? row.events!.map((event, index) => <div className="agent-event" key={index}>
            <Tag>{event.type === 'assistant' ? 'Agent 输出' : event.type === 'tool_use' ? '工具调用 · ' + (event.tool || 'unknown') : '工具结果'}</Tag>
            <pre className="json-panel">{event.type === 'tool_use' ? JSON.stringify(event.input ?? {}, null, 2) : event.text || '无可展示文本'}</pre>
          </div>) : <Empty description="没有可展示的公开事件记录" />}
        </div>} />
    </Card>
    <Drawer title={'步骤详情 · ' + (step?.title || step?.name || '')} open={Boolean(selectedStepName)} onClose={() => setSelectedStepName(undefined)} width={700} destroyOnClose>
      {step ? <div className="page-stack"><Space wrap><StatusTag status={step.status} />{step.result && <Tag>{resultLabel(step.result)}</Tag>}</Space>
        <Descriptions bordered size="small" column={2}><Descriptions.Item label="步骤名">{step.name}</Descriptions.Item><Descriptions.Item label="耗时">{duration(step.duration_ms)}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{step.started_at || '—'}</Descriptions.Item><Descriptions.Item label="结束时间">{step.finished_at || '—'}</Descriptions.Item></Descriptions>
        {step.summary && <Alert type={step.status === 'failed' ? 'error' : 'info'} showIcon message="步骤摘要" description={step.summary} />}
        {step.error && <Alert type="error" showIcon message={step.error.code || '步骤错误'} description={step.error.message || '未提供错误详情'} />}
        <div><strong>输入</strong><pre className="json-panel">{JSON.stringify(step.input ?? {}, null, 2)}</pre></div>
        <div><strong>输出</strong><pre className="json-panel">{JSON.stringify(step.output ?? {}, null, 2)}</pre></div>
      </div> : <Empty description="未找到该步骤" />}
    </Drawer>
    <Drawer title={'产物内容 · ' + (artifactPath?.name || '')} open={Boolean(artifactPath)} onClose={() => setArtifactPath(null)} width={760} destroyOnClose>
      {artifact.isLoading ? <Spin /> : artifact.isError ? <Alert type="error" message="产物加载失败" description={(artifact.error as Error).message} /> :
        <pre className="json-panel">{JSON.stringify(artifact.data?.content ?? {}, null, 2)}</pre>}
    </Drawer>
  </div>;
}
