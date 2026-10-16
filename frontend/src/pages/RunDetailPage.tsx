import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Descriptions, Drawer, Empty, Modal, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, BookOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag, statusLabel } from '../components/StatusTag';
import { WorkflowGraph } from '../components/WorkflowGraph';
import type { ModelCall, StepRecord } from '../lib/types';
const { Title, Text } = Typography;
function duration(value?: number | null) { if (value == null) return '—'; return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms'; }
function resultLabel(value?: string | null) { if (!value) return '—'; const labels: Record<string, string> = { passed: '通过', failed: '失败', FAILED: '失败', rejected: '已拒绝', REJECTED: '已拒绝', manual: '需人工介入', MANUAL: '需人工介入', PASS: '审查通过', FAIL: '审查未通过', LOCATED: '已定位根因', INSUFFICIENT_EVIDENCE: '证据不足', PENDING: '待处理', COMPLETED: '已结束', SUCCESS: '成功', SUCCEEDED: '成功', FAILED_VALIDATION: '验证失败', NOT_RUN: '未运行', INCOMPLETE: '验证不完整', STALE: '验证已过期', DELIVERED: '已交付', RUNNING: '执行中', WAITING_APPROVAL: '待审批', CLAIMED: '已领取', REPAIRING: '修复中', VERIFYING: '验证中', REVIEWING: '审查中', DELIVERING: '交付中' }; return labels[value] ?? labels[value.toUpperCase()] ?? value; }
export function RunDetailPage() {
  const { runId = '' } = useParams(); const navigate = useNavigate(); const queryClient = useQueryClient(); const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedTaskId = searchParams.get('task_id') ?? '';
  const selectTask = (taskId: string) => {
    const next = new URLSearchParams(searchParams);
    if (taskId) next.set('task_id', taskId); else next.delete('task_id');
    setSearchParams(next, { replace: true });
  };
  const [selectedStepName, setSelectedStepName] = useState<string>();
  const [selectedAgent, setSelectedAgent] = useState<string>('all');
  const [selectedArtifact, setSelectedArtifact] = useState<{ path: string; task_id?: string; name: string } | null>(null);
  const detail = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), refetchInterval: (q) => ['running', 'queued', 'waiting_approval', 'pause_requested', 'paused', 'cancel_requested'].includes(q.state.data?.status ?? '') ? 3000 : false });
  const pause = useMutation({ mutationFn: () => api.pauseRun(runId), onSuccess: async (result) => { message.info(result.message || '已提交暂停请求'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('暂停请求失败：' + (error as Error).message) });
  const resume = useMutation({ mutationFn: () => api.resumeRun(runId), onSuccess: async () => { message.success('已提交恢复请求'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('恢复执行失败：' + (error as Error).message) });
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: async (result) => { message.warning(result.message || '已提交停止请求；后台任务可能需要时间响应'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('停止请求失败：' + (error as Error).message) });
  const captureKnowledge = useMutation({ mutationFn: (taskId: string) => api.captureKnowledgeFromTask(runId, taskId), onSuccess: async () => { message.success('已从真实任务记录沉淀候选知识'); await queryClient.invalidateQueries({ queryKey: ['knowledge-cases'] }); }, onError: (error) => message.error('沉淀知识失败：' + (error as Error).message) });
  useEffect(() => {
    if (!runId) return;
    const source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events`);
    source.addEventListener('run.snapshot', () => { void queryClient.invalidateQueries({ queryKey: ['run', runId] }); });
    source.onerror = () => { /* EventSource 自动重连；详情查询仍提供快照兜底。 */ };
    return () => source.close();
  }, [runId, queryClient]);
  const data = detail.data;
  const selectedTask = data?.tasks.find((task) => task.task_id === selectedTaskId);
  const graphSteps = selectedTask ? selectedTask.steps : (data?.steps ?? []);
  const visibleArtifacts = selectedTask
    ? (data?.artifacts ?? []).filter((artifact) => artifact.task_id === selectedTask.task_id)
    : (data?.artifacts ?? []);
  const modelCalls = useQuery({ queryKey: ['model-calls', runId, selectedTaskId], queryFn: () => api.modelCalls(runId, selectedTaskId || undefined), refetchInterval: (q) => ['running', 'queued', 'waiting_approval', 'pause_requested', 'paused', 'cancel_requested'].includes(detail.data?.status ?? '') ? 5000 : false });
  const visibleModelCalls = (modelCalls.data?.items ?? []).filter((call) => selectedAgent === 'all' || call.agent_role === selectedAgent);
  const controlAudit = useQuery({ queryKey: ['control-audit', runId], queryFn: () => api.controlAudit(runId) });
  const artifactContent = useQuery({ queryKey: ['artifact-content', runId, selectedArtifact?.path, selectedArtifact?.task_id], queryFn: () => api.artifactContent(runId, selectedArtifact!.path, selectedArtifact?.task_id), enabled: Boolean(selectedArtifact) });
  const stepColumns = [
    { title: '节点', dataIndex: 'title', key: 'title', render: (v: string, row: StepRecord) => <div><strong>{v || row.name}</strong><div className="secondary-small">{row.name}</div></div> },
    { title: '执行状态', dataIndex: 'status', key: 'status', render: (v: StepRecord['status']) => <StatusTag status={v} /> },
    { title: '业务结论', dataIndex: 'result', key: 'result', render: (v?: string | null) => v ? <Tag color={String(v).toUpperCase() === 'MANUAL' ? 'orange' : undefined}>{resultLabel(v)}</Tag> : '—' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', render: duration },
    { title: '尝试次数', dataIndex: 'attempt', key: 'attempt', render: (v?: number) => v ?? 1 },
    { title: '摘要', dataIndex: 'summary', key: 'summary', render: (v?: string) => v || '—' },
    { title: '错误', dataIndex: 'error', key: 'error', render: (v?: StepRecord['error']) => v ? <span className="text-danger">{v.code}: {v.message}</span> : '—' },
  ];
  if (detail.isLoading) return <div className="page-loading"><Spin size="large" /><Text type="secondary">正在加载执行详情…</Text></div>;
  if (detail.isError || !data) return <div className="page-stack"><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>返回执行列表</Button><Alert type="error" showIcon message="无法加载执行详情" description={(detail.error as Error)?.message ?? '执行不存在或无权访问'} action={<Button onClick={() => detail.refetch()}>重试</Button>} /></div>;
  return <div className="page-stack">
    <div className="page-heading"><div><Space wrap><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>返回</Button><Title level={3} className="run-detail-title">{selectedTask ? `任务详情 · ${selectedTask.carrier}` : "执行详情"}</Title><StatusTag status={data.status} /></Space><Text type="secondary" className="run-id">{data.run_id}</Text></div>
      <Space wrap><Button icon={<ReloadOutlined />} onClick={() => detail.refetch()} loading={detail.isFetching}>刷新</Button>{['running', 'queued'].includes(data.status) && <Button onClick={() => Modal.confirm({ title: '暂停本轮执行？', content: '系统将向工作流提交暂停请求。已在运行的节点可能需要到安全检查点才会停下。', okText: '确认暂停', cancelText: '继续运行', onOk: () => pause.mutateAsync() })} loading={pause.isPending}>暂停执行</Button>}{data.checkpoint?.resumable && ['paused', 'running', 'queued'].includes(data.status) && <Button type="primary" onClick={() => Modal.confirm({ title: '恢复本轮执行？', content: '工作流会从服务端允许的恢复点继续运行，并重新校验当前状态。', okText: '恢复执行', cancelText: '暂不恢复', onOk: () => resume.mutateAsync() })} loading={resume.isPending}>恢复执行</Button>}{['running', 'queued', 'paused', 'pause_requested'].includes(data.status) && <Button danger onClick={() => Modal.confirm({ title: '提交停止请求？', content: '停止请求可能无法撤回，也不代表后台会立即中断。确认后请观察执行状态变为已取消或终止。', okText: '提交停止请求', cancelText: '返回检查', okButtonProps: { danger: true }, onOk: () => cancel.mutateAsync() })} loading={cancel.isPending}>停止执行</Button>}</Space>
    </div>
    {selectedTaskId && !selectedTask && <Alert type="error" showIcon message="任务上下文无效" description={`当前执行中不存在任务 ${selectedTaskId}。已保留 URL 参数以便排查，请从任务列表重新选择有效任务。`} action={<Button onClick={() => selectTask('')}>返回 Run 级视图</Button>} />}
    {pause.isError && <Alert type="error" showIcon message="暂停请求失败" description={(pause.error as Error).message} />}
    {resume.isError && <Alert type="error" showIcon message="恢复执行失败" description={(resume.error as Error).message} />}
    {cancel.isError && <Alert type="error" showIcon message="停止请求失败" description={(cancel.error as Error).message} />}
    {data.run_error?.status === 'FAILED' && <Alert type="error" showIcon message="工作流异常终止" description={(data.run_error.error_type || '后台异常') + '：' + (data.run_error.message || '请检查服务端日志并按执行 ID 查询完整堆栈。')} />}
    <Alert showIcon type={data.status === 'failed' || data.status === 'partial_failed' ? 'error' : data.status === 'waiting_approval' ? 'warning' : data.status === 'succeeded' ? 'success' : 'info'} message={data.status === 'failed' || data.status === 'partial_failed' ? '优先检查失败任务与对应节点错误' : data.status === 'waiting_approval' ? '本轮正在等待人工决策' : data.status === 'succeeded' ? '本轮执行已结束，可检查结果并沉淀已验证知识' : data.status === 'cancel_requested' ? '停止请求已提交，等待服务端确认最终状态' : data.status === 'paused' || data.status === 'pause_requested' ? '本轮已暂停或正在暂停，可查看断点后再决定是否恢复' : '本轮仍在执行；可先查看任务进度，节点图和审计信息用于深入排查'} description={data.status === 'waiting_approval' ? '进入待审批与人工介入页面查看方案；批准不会跳过后续验证与交付门禁。' : data.status === 'failed' || data.status === 'partial_failed' ? '在任务详情中选择失败任务，再查看对应节点的输入、输出、错误信息和产物。' : data.status === 'succeeded' ? '确认根因、回归和审查结果后，再将有效经验沉淀到知识库。' : '运行状态以服务端持久化快照为准；页面自动刷新并通过事件流更新。'} />
    <Card className="content-card"><Descriptions column={{ xs: 1, sm: 2, xl: 4 }} size="small">
      <Descriptions.Item label="开始时间">{dayjs(data.started_at).isValid() ? dayjs(data.started_at).format('YYYY-MM-DD HH:mm:ss') : '—'}</Descriptions.Item>
      <Descriptions.Item label="结束时间">{data.finished_at && dayjs(data.finished_at).isValid() ? dayjs(data.finished_at).format('YYYY-MM-DD HH:mm:ss') : '执行中'}</Descriptions.Item>
      <Descriptions.Item label={selectedTask ? "所属 Run 任务总数" : "任务总数"}>{data.task_count}</Descriptions.Item>
      <Descriptions.Item label="任务结果"><span className="text-success">{data.succeeded_tasks} 成功</span> / <span className="text-danger">{data.failed_tasks} 失败</span> / {data.manual_tasks ?? 0} 人工介入 / {data.waiting_tasks} 待审批</Descriptions.Item>
    </Descriptions></Card>
    <Card title={selectedTask ? "任务执行图 · " + selectedTask.carrier + " / " + selectedTask.biztype : "工作流执行图 · Run 级"} className="content-card" collapsible="header" defaultCollapsed extra={<Space><Select style={{ minWidth: 240 }} value={selectedTaskId} onChange={(value) => { selectTask(value); setSelectedStepName(undefined); }} options={[{ value: "", label: "查看 Run 级流程" }, ...data.tasks.map((task) => ({ value: task.task_id, label: task.carrier + " · " + task.biztype + " · " + task.task_id.slice(0, 18) }))]} /><Text type="secondary">点击节点查看输入、输出、耗时与错误</Text></Space>}><WorkflowGraph steps={graphSteps} selectedStep={selectedStepName} onSelectStep={setSelectedStepName} /></Card>
    <Drawer title="节点检查器" open={Boolean(selectedStepName)} onClose={() => setSelectedStepName(undefined)} width={680} destroyOnClose>
      {(() => { const step = graphSteps.find((item) => item.name === selectedStepName); if (!step) return <Empty description="该节点暂无持久化执行记录" />; return <div className="page-stack"><div><Title level={5}>{step.title || step.name}</Title><Text type="secondary">{step.name}</Text><div style={{ marginTop: 10 }}><StatusTag status={step.status} /> {step.result && <Tag>{resultLabel(step.result)}</Tag>}</div></div><Descriptions size="small" column={2} bordered><Descriptions.Item label="耗时">{duration(step.duration_ms)}</Descriptions.Item><Descriptions.Item label="尝试次数">{step.attempt ?? 1}</Descriptions.Item><Descriptions.Item label="开始时间">{step.started_at || "—"}</Descriptions.Item><Descriptions.Item label="结束时间">{step.finished_at || "—"}</Descriptions.Item></Descriptions>{step.summary && <Alert type={step.status === "failed" ? "error" : "info"} showIcon message="执行摘要" description={step.summary} />}{step.error && <Alert type="error" showIcon message={step.error.code || "节点错误"} description={step.error.message || "未提供错误详情"} />}<div><strong>节点输入</strong><pre className="json-panel">{JSON.stringify(step.input ?? {}, null, 2)}</pre></div><div><strong>节点输出</strong><pre className="json-panel">{JSON.stringify(step.output ?? {}, null, 2)}</pre></div></div>; })()}
    </Drawer>
    <Card className="content-card"><Tabs defaultActiveKey="tasks" items={[
      { key: 'steps', label: selectedTask ? '当前任务节点记录' : 'Run 级节点记录', children: <Table rowKey="name" columns={stepColumns} dataSource={graphSteps} pagination={false} expandable={{ expandedRowRender: (row) => <div className="step-expanded"><div><strong>输入摘要</strong><pre>{JSON.stringify(row.input ?? {}, null, 2)}</pre></div><div><strong>输出摘要</strong><pre>{JSON.stringify(row.output ?? {}, null, 2)}</pre></div></div> }} locale={{ emptyText: '暂无节点记录' }} /> },
      { key: 'tasks', label: '任务详情（' + data.tasks.length + '）', children: data.tasks.length ? <Table rowKey="task_id" dataSource={data.tasks} pagination={false} onRow={(row) => ({ onClick: () => { selectTask(row.task_id); setSelectedStepName(undefined); } })} rowClassName={(row) => row.task_id === selectedTaskId ? 'selected-task-row' : 'clickable-row'} expandable={{ expandedRowRender: (row) => <Table rowKey="name" size="small" pagination={false} dataSource={row.steps} columns={stepColumns} locale={{ emptyText: '该任务暂无节点记录' }} /> }} columns={[{ title: '任务 ID', dataIndex: 'task_id', key: 'task_id' }, { title: '运输商', dataIndex: 'carrier', key: 'carrier' }, { title: '业务类型', dataIndex: 'biztype', key: 'biztype' }, { title: '结论', dataIndex: 'result', key: 'result', render: (v: string) => <Tag color={String(v).toUpperCase() === 'MANUAL' ? 'orange' : undefined}>{resultLabel(v)}</Tag> }, { title: '节点数', key: 'steps', render: (_: unknown, row: { steps: StepRecord[] }) => row.steps?.length ?? 0 }, { title: '耗时', key: 'duration', render: (_: unknown, row: { steps: StepRecord[] }) => duration(row.steps.reduce((sum, step) => sum + (step.duration_ms ?? 0), 0)) }, { title: '操作', key: 'action', render: (_: unknown, row: { task_id: string; result: string }) => <Space><Button type="link" onClick={(event) => { event.stopPropagation(); selectTask(row.task_id); setSelectedStepName(undefined); }}>打开任务图</Button><Button type="link" icon={<BookOutlined />} disabled={['RUNNING', 'PENDING', 'REPAIRING', 'VERIFYING', 'REVIEWING', 'WAITING', 'WAITING_APPROVAL'].includes(String(row.result).toUpperCase())} loading={captureKnowledge.isPending} onClick={(event) => { event.stopPropagation(); captureKnowledge.mutate(row.task_id); }}>沉淀案例</Button></Space> }]} /> : <Empty description="本轮尚无任务" /> },
      { key: 'artifacts', label: '产物（' + visibleArtifacts.length + '）', children: visibleArtifacts.length ? <Table rowKey={(row) => (row.task_id ?? 'run') + ':' + row.path} dataSource={visibleArtifacts} pagination={{ pageSize: 10 }} columns={[{ title: '名称', dataIndex: 'name', key: 'name' }, { title: '所属步骤/任务', dataIndex: 'description', key: 'description' }, { title: '类型', dataIndex: 'media_type', key: 'media_type' }, { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', render: (v?: number) => v == null ? '按需读取' : (v / 1024).toFixed(1) + ' KB' }, { title: '操作', key: 'action', render: (_: unknown, row: { path: string; task_id?: string; name: string }) => <Button type="link" onClick={() => setSelectedArtifact(row)}>预览内容</Button> }]} /> : <Empty description="暂无产物" /> },
      { key: 'config', label: '执行配置', children: <pre className="json-panel">{JSON.stringify(data.config, null, 2)}</pre> },
    ]} /></Card>

    <Card title={"模型调用审计 · Agent 执行轨迹（" + (modelCalls.data?.total ?? 0) + "）"} className="content-card" extra={<Space><Select value={selectedAgent} onChange={setSelectedAgent} style={{ width: 150 }} options={[{ value: "all", label: "全部 Agent" }, { value: "locate", label: "根因定位 Agent" }, { value: "repair", label: "代码修复 Agent" }, { value: "review", label: "变更审查 Agent" }]} /><Button onClick={() => modelCalls.refetch()} loading={modelCalls.isFetching}>刷新审计</Button></Space>}>
      <Text type="secondary">展示实际记录到的公开消息、工具调用与工具结果；不展示模型隐藏思考链。旧运行未采集的事件不会补造。</Text>
      {modelCalls.isError && <Alert type="error" showIcon message="模型调用审计加载失败" description={(modelCalls.error as Error).message} />}
      <Table rowKey="model_call_id" loading={modelCalls.isLoading} dataSource={visibleModelCalls} pagination={{ pageSize: 8 }} locale={{ emptyText: "没有符合条件的 Agent 调用记录。" }} expandable={{ expandedRowRender: (row: ModelCall) => <div className="agent-trace">
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered><Descriptions.Item label="任务">{row.task_id || "Run 级"}</Descriptions.Item><Descriptions.Item label="尝试次数">{row.attempt ?? 1}</Descriptions.Item><Descriptions.Item label="Provider">{row.provider || "—"}</Descriptions.Item><Descriptions.Item label="Prompt 版本">{row.prompt_version || "—"}</Descriptions.Item><Descriptions.Item label="Prompt 总字符数">{row.prompt_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="系统规则字符数">{row.system_prompt_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="任务上下文字符数">{row.task_context_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="原始问题字符数">{row.problem_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="源码快照上下文字符数">{row.source_snapshot_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="预检证据字符数">{row.evidence_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="任务身份字符数">{row.identity_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="输出字符数">{row.result_chars ?? "未采集"}</Descriptions.Item><Descriptions.Item label="开始时间">{row.started_at || "—"}</Descriptions.Item><Descriptions.Item label="结束时间">{row.finished_at || "—"}</Descriptions.Item></Descriptions>
        <Title level={5}>执行事件（{row.events?.length ?? 0}）</Title>
        {(row.events ?? []).length ? <div className="agent-event-list">{row.events!.map((event, index) => <div className={"agent-event agent-event-" + event.type} key={index}><div className="agent-event-head"><Tag color={event.type === "tool_use" ? "blue" : event.type === "tool_result" ? (event.is_error ? "red" : "default") : "purple"}>{event.type === "assistant" ? "Agent 输出" : event.type === "tool_use" ? "工具调用 · " + (event.tool || "unknown") : "工具结果"}</Tag>{event.is_error && <Tag color="red">工具报错</Tag>}{event.elapsed_ms != null && <Text type="secondary">调用内 +{duration(event.elapsed_ms)}</Text>}</div>{event.type === "tool_use" ? <pre className="json-panel">{JSON.stringify(event.input ?? {}, null, 2)}</pre> : <pre className="json-panel">{event.text || "无可展示文本"}</pre>}</div>)}</div> : <Empty description={!selectedTaskId ? "请先在上方选择具体任务；Run 级默认只加载调用元数据，避免一次读取全部事件流" : row.event_persistence_status === "failed" ? "事件轨迹落盘失败；请检查后端日志中的 Agent 事件持久化错误" : row.event_persistence_status === "empty" ? "本次调用没有可保存的公开事件（例如在首个事件前超时）" : "这次调用没有持久化事件轨迹；旧记录仅有调用元数据"} />}
        {row.output_preview && <><Title level={5}>最终结构化输出</Title><pre className="json-panel">{row.output_preview}</pre></>}
      </div> }} columns={[
        { title: "Agent", dataIndex: "agent_role", key: "agent_role", render: (v: string) => ({ locate: "根因定位", repair: "代码修复", review: "变更审查" } as Record<string, string>)[v] ?? v },
        { title: "任务", dataIndex: "task_id", key: "task_id", render: (v: string) => v || "Run 级", responsive: ["lg"] },
        { title: "模型", dataIndex: "model", key: "model", render: (v: string) => v || "未返回" },
        { title: "状态", dataIndex: "status", key: "status", render: (v: string) => <Tag color={["succeeded", "success", "SUCCESS"].includes(v) ? "green" : "red"}>{statusLabel(v)}</Tag> },
        { title: "总耗时", dataIndex: "duration_ms", key: "duration_ms", render: duration },
        { title: "Token", dataIndex: "total_tokens", key: "total_tokens", render: (v?: number | null) => v == null ? "未返回" : v },
        { title: "工具调用", dataIndex: "tool_call_count", key: "tool_call_count", render: (v?: number | null) => v ?? "未返回" },
        { title: "事件", key: "events", render: (_: unknown, row: ModelCall) => row.events?.length ?? 0 },
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