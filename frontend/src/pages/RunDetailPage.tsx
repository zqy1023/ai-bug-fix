import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Empty, Modal, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import { WorkflowGraph } from '../components/WorkflowGraph';
import type { ModelCall, StepRecord } from '../lib/types';
const { Title, Text } = Typography;
function duration(value?: number | null) { if (value == null) return '—'; return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms'; }
export function RunDetailPage() {
  const { runId = '' } = useParams(); const navigate = useNavigate(); const queryClient = useQueryClient();
  const [selectedStepName, setSelectedStepName] = useState<string>();
  const [selectedArtifact, setSelectedArtifact] = useState<{ path: string; task_id?: string; name: string } | null>(null);
  const detail = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), refetchInterval: (q) => ['running', 'queued', 'waiting_approval'].includes(q.state.data?.status ?? '') ? 5000 : false });
  const resume = useMutation({ mutationFn: () => api.resumeRun(runId), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, });
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, });
  useEffect(() => {
    if (!runId) return;
    const source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events`);
    source.addEventListener('run.snapshot', () => { void queryClient.invalidateQueries({ queryKey: ['run', runId] }); });
    source.onerror = () => { /* EventSource 自动重连；详情查询仍提供快照兜底。 */ };
    return () => source.close();
  }, [runId, queryClient]);
  const data = detail.data;
  const modelCalls = useQuery({ queryKey: ['model-calls', runId], queryFn: () => api.modelCalls(runId), refetchInterval: (q) => ['running', 'queued', 'waiting_approval', 'cancel_requested'].includes(detail.data?.status ?? '') ? 5000 : false });
  const controlAudit = useQuery({ queryKey: ['control-audit', runId], queryFn: () => api.controlAudit(runId) });
  const artifactContent = useQuery({ queryKey: ['artifact-content', runId, selectedArtifact?.path, selectedArtifact?.task_id], queryFn: () => api.artifactContent(runId, selectedArtifact!.path, selectedArtifact?.task_id), enabled: Boolean(selectedArtifact) });
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
    <Card title="工作流执行图" className="content-card" extra={<Text type="secondary">点击节点后可检查真实输入、输出与错误</Text>}><WorkflowGraph steps={data.steps} selectedStep={selectedStepName} onSelectStep={setSelectedStepName} /></Card>
    <Drawer title="节点检查器" open={Boolean(selectedStepName)} onClose={() => setSelectedStepName(undefined)} width={680} destroyOnClose>
      {(() => { const step = data.steps.find((item) => item.name === selectedStepName); if (!step) return <Empty description="该节点暂无持久化执行记录" />; return <div className="page-stack"><div><Title level={5}>{step.title || step.name}</Title><Text type="secondary">{step.name}</Text><div style={{ marginTop: 10 }}><StatusTag status={step.status} /> {step.result && <Tag>{step.result}</Tag>}</div></div><Descriptions size="small" column={2} bordered><Descriptions.Item label="耗时">{duration(step.duration_ms)}</Descriptions.Item><Descriptions.Item label="尝试次数">{step.attempt ?? 1}</Descriptions.Item><Descriptions.Item label="开始时间">{step.started_at || "—"}</Descriptions.Item><Descriptions.Item label="结束时间">{step.finished_at || "—"}</Descriptions.Item></Descriptions>{step.summary && <Alert type={step.status === "failed" ? "error" : "info"} showIcon message="执行摘要" description={step.summary} />}{step.error && <Alert type="error" showIcon message={step.error.code || "节点错误"} description={step.error.message || "未提供错误详情"} />}<div><strong>节点输入</strong><pre className="json-panel">{JSON.stringify(step.input ?? {}, null, 2)}</pre></div><div><strong>节点输出</strong><pre className="json-panel">{JSON.stringify(step.output ?? {}, null, 2)}</pre></div></div>; })()}
    </Drawer>
    <Card className="content-card"><Tabs items={[
      { key: 'steps', label: '节点记录', children: <Table rowKey="name" columns={stepColumns} dataSource={data.steps} pagination={false} expandable={{ expandedRowRender: (row) => <div className="step-expanded"><div><strong>输入摘要</strong><pre>{JSON.stringify(row.input ?? {}, null, 2)}</pre></div><div><strong>输出摘要</strong><pre>{JSON.stringify(row.output ?? {}, null, 2)}</pre></div></div> }} locale={{ emptyText: '暂无节点记录' }} /> },
      { key: 'tasks', label: '任务列表（' + data.tasks.length + '）', children: data.tasks.length ? <Table rowKey="task_id" dataSource={data.tasks} pagination={false} columns={[{ title: '任务 ID', dataIndex: 'task_id', key: 'task_id' }, { title: '运输商', dataIndex: 'carrier', key: 'carrier' }, { title: '业务类型', dataIndex: 'biztype', key: 'biztype' }, { title: '结论', dataIndex: 'result', key: 'result', render: (v: string) => <Tag>{v}</Tag> }, { title: '节点数', key: 'steps', render: (_: unknown, row: { steps: StepRecord[] }) => row.steps?.length ?? 0 }]} /> : <Empty description="本轮尚无任务" /> },
      { key: 'artifacts', label: '产物（' + data.artifacts.length + '）', children: data.artifacts.length ? <Table rowKey={(row) => (row.task_id ?? 'run') + ':' + row.path} dataSource={data.artifacts} pagination={{ pageSize: 10 }} columns={[{ title: '名称', dataIndex: 'name', key: 'name' }, { title: '所属步骤/任务', dataIndex: 'description', key: 'description' }, { title: '类型', dataIndex: 'media_type', key: 'media_type' }, { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', render: (v?: number) => v == null ? '按需读取' : (v / 1024).toFixed(1) + ' KB' }, { title: '操作', key: 'action', render: (_: unknown, row: { path: string; task_id?: string; name: string }) => <Button type="link" onClick={() => setSelectedArtifact(row)}>预览内容</Button> }]} /> : <Empty description="暂无产物" /> },
      { key: 'config', label: '执行配置', children: <pre className="json-panel">{JSON.stringify(data.config, null, 2)}</pre> },
    ]} /></Card>

    <Card title={"模型调用审计（" + (modelCalls.data?.total ?? 0) + "）"} className="content-card" extra={<Button onClick={() => modelCalls.refetch()} loading={modelCalls.isFetching}>刷新调用</Button>}>
      {modelCalls.isError && <Alert type="error" showIcon message="模型调用审计加载失败" description={(modelCalls.error as Error).message} />}
      <Table rowKey="model_call_id" loading={modelCalls.isLoading} dataSource={modelCalls.data?.items ?? []} pagination={{ pageSize: 10 }} locale={{ emptyText: "没有调用级审计记录。历史运行不会补造模型调用数据。" }} columns={[
        { title: "Agent", dataIndex: "agent_role", key: "agent_role" },
        { title: "模型", dataIndex: "model", key: "model", render: (v: string) => v || "未返回" },
        { title: "状态", dataIndex: "status", key: "status", render: (v: string) => <Tag color={v === "succeeded" ? "green" : "red"}>{v}</Tag> },
        { title: "耗时", dataIndex: "duration_ms", key: "duration_ms", render: duration },
        { title: "Token", key: "tokens", render: (_: unknown, row: ModelCall) => row.total_tokens == null ? "未返回" : row.total_tokens },
        { title: "工具调用", dataIndex: "tool_call_count", key: "tool_call_count", render: (v?: number | null) => v ?? "未返回" },
        { title: "Prompt 哈希", dataIndex: "prompt_hash", key: "prompt_hash", render: (v?: string) => v ? v.slice(0, 12) : "—" },
        { title: "失败原因", dataIndex: "error_type", key: "error_type", render: (v?: string) => v || "—" },
      ]} />
    </Card>
    <Modal title={selectedArtifact ? '产物预览 · ' + selectedArtifact.name : '产物预览'} open={Boolean(selectedArtifact)} onCancel={() => setSelectedArtifact(null)} footer={<Button onClick={() => setSelectedArtifact(null)}>关闭</Button>} width={900} destroyOnClose>
      {artifactContent.isLoading ? <Spin /> : artifactContent.isError ? <Alert type="error" showIcon message="产物读取失败" description={(artifactContent.error as Error).message} /> : <pre className="json-panel">{JSON.stringify(artifactContent.data?.content ?? null, null, 2)}</pre>}
    </Modal>
    <Card title={"控制操作审计（" + (controlAudit.data?.total ?? 0) + "）"} className="content-card">
      <Table rowKey="id" dataSource={controlAudit.data?.items ?? []} loading={controlAudit.isLoading} pagination={{ pageSize: 10 }} locale={{ emptyText: "暂无控制操作记录" }} columns={[
        { title: "时间", dataIndex: "created_at", key: "created_at", render: (v: string) => dayjs(v).isValid() ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "—" },
        { title: "操作", dataIndex: "action", key: "action" }, { title: "操作者", dataIndex: "actor", key: "actor" },
        { title: "任务", dataIndex: "task_id", key: "task_id" }, { title: "原因/参数", dataIndex: "reason", key: "reason", render: (v: string) => v || "—" },
      ]} />
    </Card>
  </div>;
}