import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Collapse, Descriptions, Drawer, Empty, Modal, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import { WorkflowGraph } from '../components/WorkflowGraph';
import type { StepRecord } from '../lib/types';
const { Title, Text, Paragraph } = Typography;
function duration(value?: number | null) { if (value == null) return '—'; return value >= 1000 ? (value / 1000).toFixed(2) + ' 秒' : value.toFixed(0) + ' ms'; }
function resultLabel(value?: string | null) { if (!value) return '—'; const labels: Record<string, string> = { passed: '通过', failed: '失败', FAILED: '失败', rejected: '已拒绝', REJECTED: '已拒绝', manual: '需人工判断', MANUAL: '需人工判断', PASS: '审查通过', FAIL: '审查未通过', LOCATED: '已定位根因', INSUFFICIENT_EVIDENCE: '证据不足', PENDING: '待处理', COMPLETED: '已结束', SUCCESS: '成功', SUCCEEDED: '成功', FAILED_VALIDATION: '验证失败', NOT_RUN: '未运行', INCOMPLETE: '验证不完整', STALE: '验证已过期', NO_TASKS: '本轮无任务', PARTIAL_FAILED: '部分失败', CANCELLED: '已取消', DELIVERED: '已交付', RUNNING: '执行中', WAITING_APPROVAL: '待审批', CLAIMED: '已领取', REPAIRING: '修复中', VERIFYING: '验证中', REVIEWING: '审查中', DELIVERING: '交付中' }; return labels[value] ?? labels[value.toUpperCase()] ?? value; }
export function RunDetailPage() {
  const { runId = '' } = useParams(); const navigate = useNavigate(); const queryClient = useQueryClient(); const { message } = App.useApp();
  const selectTask = (taskId: string) => navigate(taskId
    ? `/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}`
    : `/runs/${encodeURIComponent(runId)}`);
  const [selectedStepName, setSelectedStepName] = useState<string>();
  const [activeTab, setActiveTab] = useState('steps');
  const [selectedArtifact, setSelectedArtifact] = useState<{ path: string; task_id?: string; name: string } | null>(null);
  const [resumeAwaitingTransition, setResumeAwaitingTransition] = useState(false);
  const detail = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), refetchInterval: (q) => (resumeAwaitingTransition || ['running', 'queued', 'waiting_approval', 'pause_requested', 'paused', 'cancel_requested'].includes(q.state.data?.status ?? '')) ? 3000 : false });
  const pause = useMutation({ mutationFn: () => api.pauseRun(runId), onSuccess: async (result) => { message.info(result.message || '已提交暂停请求'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('暂停请求失败：' + (error as Error).message) });
  const resume = useMutation({ mutationFn: () => api.resumeRun(runId), onSuccess: async (result) => { setResumeAwaitingTransition(true); message.success(result.status === 'resume_requested' ? (result.message || '已提交检查点恢复，正在等待工作流状态更新') : '恢复请求已返回'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('恢复执行失败：' + (error as Error).message) });
  useEffect(() => {
    if (!resumeAwaitingTransition) return;
    if (detail.data && !['failed', 'paused'].includes(detail.data.status)) {
      setResumeAwaitingTransition(false);
      return;
    }
    // API 的 202 仅表示已排队；继续轮询一段时间，避免页面停留在旧的 failed/paused 快照。
    const timeout = window.setTimeout(() => setResumeAwaitingTransition(false), 60_000);
    return () => window.clearTimeout(timeout);
  }, [resumeAwaitingTransition, detail.data?.status]);
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: async (result) => { message.warning(result.message || '已提交停止请求；后台任务可能需要时间响应'); await queryClient.invalidateQueries({ queryKey: ['run', runId] }); }, onError: (error) => message.error('停止请求失败：' + (error as Error).message) });
  // 同一时刻只允许一个控制动作，避免暂停/恢复/停止请求互相竞争。
  const controlPending = pause.isPending || resume.isPending || cancel.isPending;
  useEffect(() => {
    if (!runId) return;
    const source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events`);
    source.addEventListener('run.snapshot', () => { void queryClient.invalidateQueries({ queryKey: ['run', runId] }); });
    source.onerror = () => { /* EventSource 自动重连；详情查询仍提供快照兜底。 */ };
    return () => source.close();
  }, [runId, queryClient]);
  const data = detail.data;
  const graphSteps = data?.steps ?? [];
  const visibleArtifacts = data?.artifacts ?? [];
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
    <div className="page-heading run-heading"><div><Space wrap><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/runs')}>返回</Button><Title level={3} className="run-detail-title">执行详情</Title><StatusTag status={data.status} /></Space><Text type="secondary" className="run-id">{data.run_id}</Text></div>
      <div className="run-control-toolbar">
        <Button icon={<ReloadOutlined />} onClick={() => detail.refetch()} loading={detail.isFetching}>刷新</Button>
        {['running', 'queued'].includes(data.status) && <Button disabled={controlPending} onClick={() => Modal.confirm({ title: data.status === 'queued' ? '暂停排队中的执行？' : '暂停本轮执行？', content: '系统会请求工作流在安全节点边界暂停。当前正在运行的节点可能需要先完成，状态变为“暂停中”不代表已经暂停成功。', okText: '确认请求暂停', cancelText: '取消', onOk: () => pause.mutateAsync() })} loading={pause.isPending}>请求暂停</Button>}
        {data.status === 'pause_requested' && <Button disabled loading>等待安全节点暂停</Button>}
        {data.status === 'paused' && data.checkpoint?.resumable && <Button type="primary" disabled={controlPending} onClick={() => Modal.confirm({ title: '恢复本轮执行？', content: '从服务端确认的可恢复检查点继续执行。恢复后将重新进入运行状态。', okText: '确认恢复', cancelText: '暂不恢复', onOk: () => resume.mutateAsync() })} loading={resume.isPending}>恢复执行</Button>}
        {data.status === 'failed' && data.checkpoint?.resumable && <Button type="primary" disabled={controlPending} onClick={() => Modal.confirm({ title: '从检查点恢复失败的执行？', content: '系统会从服务端确认的 checkpoint 继续；不会从头重跑。发生写入或交付副作用的节点仍由原有门禁负责。', okText: '确认恢复', cancelText: '取消', onOk: () => resume.mutateAsync() })} loading={resume.isPending}>从检查点恢复</Button>}
        {data.status === 'paused' && !data.checkpoint?.resumable && <Button disabled>暂无可恢复检查点</Button>}
        {data.status === 'cancel_requested' && <Button danger disabled loading>等待停止生效</Button>}
        {['running', 'queued', 'paused', 'pause_requested'].includes(data.status) && <Button danger disabled={controlPending} onClick={() => Modal.confirm({ title: '停止本轮执行？', content: '停止是终止操作，不等同于暂停；后台可能需要在安全边界收尾后才能结束。此请求提交后请等待服务端确认最终状态。', okText: '确认停止', cancelText: '继续检查', okButtonProps: { danger: true }, onOk: () => cancel.mutateAsync() })} loading={cancel.isPending}>停止执行</Button>}
      </div>
    </div>
    {['running', 'queued'].includes(data.status) && <div className="run-control-hint"><Text type="secondary">当前可请求暂停；暂停请求生效后，操作区会切换为“恢复执行”。暂停与恢复不会同时出现。</Text></div>}
    {data.status === 'pause_requested' && <div className="run-control-hint"><Text type="secondary">暂停请求已提交，工作流正在等待安全节点边界。此时不能恢复；请等待状态变为“已暂停”。</Text></div>}
    {data.status === 'paused' && <div className="run-control-hint"><Text type="secondary">{data.checkpoint?.resumable ? '执行已暂停，可从可恢复检查点继续。' : '执行已暂停，但服务端尚未提供可恢复检查点，因此暂不开放恢复操作。'}</Text></div>}
    {data.status === 'failed' && data.checkpoint?.resumable && <div className="run-control-hint"><Text type="secondary">工作流异常退出，但服务端保留了可恢复检查点。可尝试恢复；若失败节点存在副作用，恢复仍会受现有门禁约束。</Text></div>}
    {data.status === 'cancel_requested' && <div className="run-control-hint"><Text type="secondary">停止请求已提交，正在等待后台任务到达安全边界并确认终态。</Text></div>}
    {pause.isError && <Alert type="error" showIcon message="暂停请求失败" description={(pause.error as Error).message} />}
    {resume.isError && <Alert type="error" showIcon message="恢复执行失败" description={(resume.error as Error).message} />}
    {cancel.isError && <Alert type="error" showIcon message="停止请求失败" description={(cancel.error as Error).message} />}
    {data.run_error?.status === 'FAILED' && <Alert type="error" showIcon message="工作流异常终止" description={(data.run_error.error_type || '后台异常') + '：' + (data.run_error.message || '请检查服务端日志并按执行 ID 查询完整堆栈。')} />}
    {data.run_report?.terminal_reason && <Card className="content-card" title="本轮最终结果">
      <Space wrap><Tag color={['FAILED', 'PARTIAL_FAILED', 'MANUAL', 'INCOMPLETE'].includes(String(data.run_report.result).toUpperCase()) ? 'orange' : 'green'}>{resultLabel(data.run_report.result)}</Tag><Text type="secondary">{data.run_report.terminal_step || 'workflow'} · {data.run_report.finished_at ? dayjs(data.run_report.finished_at).format('YYYY-MM-DD HH:mm:ss') : '—'}</Text></Space>
      <Paragraph style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>{data.run_report.terminal_reason}</Paragraph>
      <Text strong>建议下一步：</Text><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{data.run_report.next_action || '打开对应任务查看终态报告。'}</Paragraph>
    </Card>}
    <Alert showIcon type={data.status === 'failed' || data.status === 'partial_failed' ? 'error' : data.status === 'waiting_approval' ? 'warning' : data.status === 'succeeded' ? 'success' : 'info'} message={data.status === 'failed' || data.status === 'partial_failed' ? '优先检查失败任务与对应节点错误' : data.status === 'waiting_approval' ? '本轮正在等待人工决策' : data.status === 'succeeded' ? '本轮执行已结束，可检查结果与交付记录' : data.status === 'cancel_requested' ? '停止请求已提交，等待服务端确认最终状态' : data.status === 'paused' || data.status === 'pause_requested' ? '本轮已暂停或正在暂停，可查看断点后再决定是否恢复' : '本轮仍在执行；可先查看任务进度，节点图和审计信息用于深入排查'} description={data.status === 'waiting_approval' ? '进入最终审批页面查看已完成验证、回归和审查的交付方案；决定会记录到服务端审批审计。' : data.status === 'failed' || data.status === 'partial_failed' ? '在任务详情中选择失败任务，再查看对应节点的输入、输出、错误信息和产物。' : '运行状态以服务端持久化快照为准；页面自动刷新并通过事件流更新。'} />
    <Card title="Run 级执行概览" className="content-card"><Descriptions column={{ xs: 1, sm: 2, xl: 4 }} size="small">
      <Descriptions.Item label="开始时间">{dayjs(data.started_at).isValid() ? dayjs(data.started_at).format('YYYY-MM-DD HH:mm:ss') : '—'}</Descriptions.Item>
      <Descriptions.Item label="结束时间">{data.finished_at && dayjs(data.finished_at).isValid() ? dayjs(data.finished_at).format('YYYY-MM-DD HH:mm:ss') : '执行中'}</Descriptions.Item>
      <Descriptions.Item label="任务总数">{data.task_count}</Descriptions.Item>
      <Descriptions.Item label="任务结果"><span className="text-success">{data.succeeded_tasks} 成功</span> / <span className="text-danger">{data.failed_tasks} 失败</span> / {data.manual_tasks ?? 0} 需人工判断 / {data.waiting_tasks} 待审批</Descriptions.Item>
    </Descriptions></Card>
    <Card title={"任务结果（" + data.tasks.length + "）"} className="content-card">
      {data.tasks.length ? <Table rowKey="task_id" dataSource={data.tasks} pagination={{ pageSize: 10, showSizeChanger: false }} onRow={(row) => ({ onClick: () => selectTask(row.task_id) })} rowClassName={() => 'clickable-row'} columns={[
        { title: '运输商', dataIndex: 'carrier', key: 'carrier' },
        { title: '业务类型', dataIndex: 'biztype', key: 'biztype' },
        { title: '结论', dataIndex: 'result', key: 'result', render: (v: string) => <Tag color={['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(String(v).toUpperCase()) ? 'orange' : undefined}>{resultLabel(v)}</Tag> },
        { title: '结束 / 当前步骤', dataIndex: 'terminal_step', key: 'terminal_step', render: (v?: string) => v || '进行中' },
        { title: '结束原因', dataIndex: 'terminal_reason', key: 'terminal_reason', render: (v?: string) => <Text ellipsis={{ tooltip: v }} style={{ maxWidth: 420, display: 'inline-block' }}>{v || '尚未结束'}</Text> },
        { title: '操作', key: 'action', render: (_: unknown, row: { task_id: string }) => <Button type="link" onClick={(event) => { event.stopPropagation(); selectTask(row.task_id); }}>查看任务详情</Button> }
      ]} /> : <Empty description="本轮尚无任务" />}
    </Card>
    <Collapse className="content-card" items={[{ key: "audit", label: "Run 级诊断与审计（按需展开）", children: <>
    <Card title="Run 级工作流" className="content-card" extra={<Text type="secondary">点击节点查看输入、输出、耗时与错误</Text>}><WorkflowGraph steps={graphSteps} selectedStep={selectedStepName} onSelectStep={setSelectedStepName} /></Card>
    <Drawer title="节点检查器" open={Boolean(selectedStepName)} onClose={() => setSelectedStepName(undefined)} width={680} destroyOnClose>
      {(() => { const step = graphSteps.find((item) => item.name === selectedStepName); if (!step) return <Empty description="该节点暂无持久化执行记录" />; return <div className="page-stack"><div><Title level={5}>{step.title || step.name}</Title><Text type="secondary">{step.name}</Text><div style={{ marginTop: 10 }}><StatusTag status={step.status} /> {step.result && <Tag>{resultLabel(step.result)}</Tag>}</div></div><Descriptions size="small" column={2} bordered><Descriptions.Item label="耗时">{duration(step.duration_ms)}</Descriptions.Item><Descriptions.Item label="尝试次数">{step.attempt ?? 1}</Descriptions.Item><Descriptions.Item label="开始时间">{step.started_at || "—"}</Descriptions.Item><Descriptions.Item label="结束时间">{step.finished_at || "—"}</Descriptions.Item></Descriptions>{step.summary && <Alert type={step.status === "failed" ? "error" : "info"} showIcon message="执行摘要" description={step.summary} />}{step.error && <Alert type="error" showIcon message={step.error.code || "节点错误"} description={step.error.message || "未提供错误详情"} />}<div><strong>节点输入</strong><pre className="json-panel">{JSON.stringify(step.input ?? {}, null, 2)}</pre></div><div><strong>节点输出</strong><pre className="json-panel">{JSON.stringify(step.output ?? {}, null, 2)}</pre></div></div>; })()}
    </Drawer>
    <Card className="content-card"><Tabs activeKey={activeTab} onChange={setActiveTab} items={[
      { key: 'steps', label: 'Run 级节点记录', children: <Table rowKey="name" columns={stepColumns} dataSource={graphSteps} pagination={false} expandable={{ expandedRowRender: (row) => <div className="step-expanded"><div><strong>输入摘要</strong><pre>{JSON.stringify(row.input ?? {}, null, 2)}</pre></div><div><strong>输出摘要</strong><pre>{JSON.stringify(row.output ?? {}, null, 2)}</pre></div></div> }} locale={{ emptyText: '暂无节点记录' }} /> },
      { key: 'artifacts', label: 'Run 级产物（' + visibleArtifacts.length + '）', children: visibleArtifacts.length ? <Table rowKey={(row) => (row.task_id ?? 'run') + ':' + row.path} dataSource={visibleArtifacts} pagination={{ pageSize: 10 }} columns={[{ title: '名称', dataIndex: 'name', key: 'name' }, { title: '所属步骤/任务', dataIndex: 'description', key: 'description' }, { title: '类型', dataIndex: 'media_type', key: 'media_type' }, { title: '大小', dataIndex: 'size_bytes', key: 'size_bytes', render: (v?: number) => v == null ? '按需读取' : (v / 1024).toFixed(1) + ' KB' }, { title: '操作', key: 'action', render: (_: unknown, row: { path: string; task_id?: string; name: string }) => <Button type="link" onClick={() => setSelectedArtifact(row)}>预览内容</Button> }]} /> : <Empty description="暂无产物" /> },
      { key: 'config', label: '执行配置', children: <pre className="json-panel">{JSON.stringify(data.config, null, 2)}</pre> },
    ]} /></Card>
    <Modal title={selectedArtifact ? '产物预览 · ' + selectedArtifact.name : '产物预览'} open={Boolean(selectedArtifact)} onCancel={() => setSelectedArtifact(null)} footer={<Button onClick={() => setSelectedArtifact(null)}>关闭</Button>} width={900} destroyOnClose>
      {artifactContent.isLoading ? <Spin /> : artifactContent.isError ? <Alert type="error" showIcon message="产物读取失败" description={(artifactContent.error as Error).message} /> : <pre className="json-panel">{JSON.stringify(artifactContent.data?.content ?? null, null, 2)}</pre>}
    </Modal>
    <Card title={"Run 级控制操作审计（" + (controlAudit.data?.total ?? 0) + "）"} className="content-card">
      <Table rowKey="id" dataSource={controlAudit.data?.items ?? []} loading={controlAudit.isLoading} pagination={{ pageSize: 10 }} locale={{ emptyText: "暂无控制操作记录" }} columns={[
        { title: "时间", dataIndex: "created_at", key: "created_at", render: (v: string) => dayjs(v).isValid() ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "—" },
        { title: "操作", dataIndex: "action", key: "action" }, { title: "操作者", dataIndex: "actor", key: "actor" },
        { title: "任务", dataIndex: "task_id", key: "task_id" }, { title: "原因/参数", dataIndex: "reason", key: "reason", render: (v: string) => v || "—" },
      ]} />
    </Card>
    </> }]} />
  </div>;
}
