import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Input, Modal, Space, Table, Tabs, Tag, Typography } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import type { ApprovalItem, ManualIntervention } from '../lib/types';
const { Title, Text } = Typography;
const { TextArea } = Input;
export function ApprovalsPage() {
  const { message } = App.useApp(); const queryClient = useQueryClient(); const navigate = useNavigate();
  const [selected, setSelected] = useState<ApprovalItem | null>(null); const [decision, setDecision] = useState<'approve' | 'reject'>('approve'); const [reason, setReason] = useState('');
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000 });
  const manual = useQuery({ queryKey: ['manual-interventions'], queryFn: api.manualInterventions, refetchInterval: 15000 });
  const mutation = useMutation({
    mutationFn: () => api.decideApproval(selected!.approval_id, decision, reason.trim()),
    onSuccess: async () => { message.success(decision === 'approve' ? '审批已提交' : '已提交拒绝决定'); setSelected(null); setReason(''); await Promise.all([queryClient.invalidateQueries({ queryKey: ['approvals'] }), queryClient.invalidateQueries({ queryKey: ['manual-interventions'] }), queryClient.invalidateQueries({ queryKey: ['dashboard'] })]); },
    onError: (error) => message.error('审批提交失败：' + (error as Error).message),
  });
  const columns = [
    { title: '运输商', dataIndex: 'carrier', key: 'carrier', render: (v: string, row: ApprovalItem) => <div><strong>{v}</strong><div className="secondary-small">{row.biztype}</div></div> },
    { title: '变更范围', dataIndex: 'scope', key: 'scope', render: (v: string) => <Tag color={v === 'project' ? 'purple' : 'blue'}>{v === 'project' ? '共享层变更' : '运输商内变更'}</Tag> },
    { title: '修复摘要', dataIndex: 'summary', key: 'summary', width: '34%' },
    { title: '变更文件', dataIndex: 'changed_files', key: 'changed_files', render: (v: string[]) => (v?.length ?? 0) + ' 个文件' },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '操作', key: 'action', render: (_: unknown, row: ApprovalItem) => <Space wrap><Button size="small" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id) + '?task_id=' + encodeURIComponent(row.task_id))}>查看执行证据</Button><Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => { setSelected(row); setDecision('approve'); setReason(''); }}>审批</Button><Button size="small" danger icon={<CloseOutlined />} onClick={() => { setSelected(row); setDecision('reject'); setReason(''); }}>拒绝</Button></Space> },
  ];
  return <div className="page-stack">
    <div className="page-heading"><div><Title level={3}>人工审批</Title><Text type="secondary">审批只对当前方案授权；批准后仍必须通过后续验证与交付门禁。</Text></div><Button icon={<ReloadOutlined />} onClick={() => approvals.refetch()} loading={approvals.isFetching}>刷新</Button></div>
    {approvals.isError && <Alert type="error" showIcon message="审批列表加载失败" description={(approvals.error as Error).message} action={<Button type="link" onClick={() => approvals.refetch()}>重试</Button>} />}
    <Tabs items={[
      { key: 'approvals', label: '待审批方案（' + (approvals.data?.length ?? 0) + '）', children: <Card className="content-card" title="待审批变更方案"><Table rowKey="approval_id" loading={approvals.isLoading} columns={columns} dataSource={approvals.data ?? []} pagination={{ pageSize: 10, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '当前没有待处理审批' }} /></Card> },
      { key: 'manual', label: '人工介入（' + (manual.data?.length ?? 0) + '）', children: <Card className="content-card" title="需要人工排查的任务"><Alert type="info" showIcon message="人工介入不等于变更审批" description="这些任务因证据不足、范围冲突或审查结论为 MANUAL 而停止自动流程。这里用于接手排查；没有可批准的变更方案，因此不会出现在待审批方案列表。" style={{ marginBottom: 16 }} />{manual.isError && <Alert type="error" showIcon message="人工介入队列加载失败" description={(manual.error as Error).message} />}<Table rowKey="intervention_id" loading={manual.isLoading} dataSource={manual.data ?? []} pagination={{ pageSize: 10 }} locale={{ emptyText: '当前没有待人工介入任务' }} columns={[
        { title: '运输商', dataIndex: 'carrier', key: 'carrier', render: (v: string, row: ManualIntervention) => <div><strong>{v || '未知运输商'}</strong><div className="secondary-small">{row.biztype}</div></div> },
        { title: '触发节点', dataIndex: 'step', key: 'step', render: (v: string) => ({ locate_agent: '根因定位', review_agent: '变更审查', finalize_execution: '执行收尾' } as Record<string, string>)[v] ?? v },
        { title: '介入原因', dataIndex: 'summary', key: 'summary', width: '42%' },
        { title: '任务 ID', dataIndex: 'task_id', key: 'task_id', responsive: ['lg'] },
        { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
        { title: '操作', key: 'action', render: (_: unknown, row: ManualIntervention) => <Button type="primary" ghost size="small" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>查看执行与证据</Button> },
      ]} /></Card> },
    ]} />
    <Modal title={decision === 'approve' ? '确认批准变更方案' : '确认拒绝变更方案'} open={Boolean(selected)} onCancel={() => !mutation.isPending && setSelected(null)} onOk={() => mutation.mutate()} confirmLoading={mutation.isPending} okText={decision === 'approve' ? '确认批准' : '确认拒绝'} okButtonProps={{ danger: decision === 'reject', disabled: decision === 'reject' && !reason.trim() }} cancelText="返回检查" destroyOnClose>
      {selected && <>
        <Alert type={decision === 'approve' ? 'warning' : 'error'} showIcon message={decision === 'approve' ? '批准不代表跳过验证或自动合并' : '拒绝后工作流按既定重试与终止规则处理'} className="modal-alert" />
        <div className="approval-summary"><div><Text type="secondary">运输商 / 业务类型</Text><div><strong>{selected.carrier}</strong> · {selected.biztype}</div></div><div><Text type="secondary">变更范围</Text><div>{selected.scope === 'project' ? '共享项目代码（高风险，确认影响范围）' : '单个运输商范围'}</div></div><div><Text type="secondary">来源执行 / 任务</Text><div><code>{selected.run_id}</code><div><code>{selected.task_id}</code></div><Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/runs/' + encodeURIComponent(selected.run_id) + '?task_id=' + encodeURIComponent(selected.task_id))}>打开执行详情，检查根因、节点与验证报告</Button></div></div><div><Text type="secondary">变更文件（{selected.changed_files.length}）</Text><ul>{selected.changed_files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div><div><Text type="secondary">方案摘要</Text><p>{selected.summary}</p></div>{selected.expires_at && <div><Text type="secondary">审批截止时间</Text><div>{dayjs(selected.expires_at).isValid() ? dayjs(selected.expires_at).format('YYYY-MM-DD HH:mm:ss') : selected.expires_at}</div></div>}</div>
        <label className="form-label">{decision === 'approve' ? '审批意见（选填）' : '拒绝原因（必填）'}</label>
        <TextArea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} showCount placeholder={decision === 'approve' ? '记录审批依据，便于审计追踪' : '请说明拒绝原因，以便后续修复和审查'} />
      </>}
    </Modal>
  </div>;
}