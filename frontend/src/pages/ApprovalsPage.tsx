import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Input, Modal, Space, Table, Tag, Typography } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { api, isDemoMode } from '../lib/api';
import type { ApprovalItem } from '../lib/types';
const { Title, Text } = Typography;
const { TextArea } = Input;
export function ApprovalsPage() {
  const { message } = App.useApp(); const queryClient = useQueryClient();
  const [selected, setSelected] = useState<ApprovalItem | null>(null); const [decision, setDecision] = useState<'approve' | 'reject'>('approve'); const [reason, setReason] = useState('');
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000 });
  const mutation = useMutation({
    mutationFn: () => api.decideApproval(selected!.approval_id, decision, reason.trim()),
    onSuccess: async () => { message.success(decision === 'approve' ? '审批已提交' : '已提交拒绝决定'); setSelected(null); setReason(''); await Promise.all([queryClient.invalidateQueries({ queryKey: ['approvals'] }), queryClient.invalidateQueries({ queryKey: ['dashboard'] })]); },
    onError: (error) => message.error('审批提交失败：' + (error as Error).message),
  });
  const columns = [
    { title: '运输商', dataIndex: 'carrier', key: 'carrier', render: (v: string, row: ApprovalItem) => <div><strong>{v}</strong><div className="secondary-small">{row.biztype}</div></div> },
    { title: '变更范围', dataIndex: 'scope', key: 'scope', render: (v: string) => <Tag color={v === 'project' ? 'purple' : 'blue'}>{v === 'project' ? '共享层变更' : '运输商内变更'}</Tag> },
    { title: '修复摘要', dataIndex: 'summary', key: 'summary', width: '34%' },
    { title: '变更文件', dataIndex: 'changed_files', key: 'changed_files', render: (v: string[]) => (v?.length ?? 0) + ' 个文件' },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '操作', key: 'action', render: (_: unknown, row: ApprovalItem) => <Space><Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => { setSelected(row); setDecision('approve'); setReason(''); }}>审批</Button><Button size="small" danger icon={<CloseOutlined />} onClick={() => { setSelected(row); setDecision('reject'); setReason(''); }}>拒绝</Button></Space> },
  ];
  return <div className="page-stack">
    <div className="page-heading"><div><Title level={3}>人工审批</Title><Text type="secondary">审批只对当前方案授权；批准后仍必须通过后续验证与交付门禁。</Text></div><Button icon={<ReloadOutlined />} onClick={() => approvals.refetch()} loading={approvals.isFetching}>刷新</Button></div>
    {isDemoMode && <Alert type="warning" showIcon message="演示模式下审批队列为空" description="演示数据不会创建真实审批，也不会触发工作流副作用。" />}
    {approvals.isError && <Alert type="error" showIcon message="审批列表加载失败" description={(approvals.error as Error).message} action={<Button type="link" onClick={() => approvals.refetch()}>重试</Button>} />}
    <Card className="content-card" title={'待处理审批（' + (approvals.data?.length ?? 0) + '）'}><Table rowKey="approval_id" loading={approvals.isLoading} columns={columns} dataSource={approvals.data ?? []} pagination={{ pageSize: 10, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '当前没有待处理审批' }} /></Card>
    <Modal title={decision === 'approve' ? '确认批准变更方案' : '确认拒绝变更方案'} open={Boolean(selected)} onCancel={() => !mutation.isPending && setSelected(null)} onOk={() => mutation.mutate()} confirmLoading={mutation.isPending} okText={decision === 'approve' ? '确认批准' : '确认拒绝'} okButtonProps={{ danger: decision === 'reject', disabled: decision === 'reject' && !reason.trim() }} cancelText="返回检查" destroyOnClose>
      {selected && <>
        <Alert type={decision === 'approve' ? 'warning' : 'error'} showIcon message={decision === 'approve' ? '批准不代表跳过验证或自动合并' : '拒绝后工作流按既定重试与终止规则处理'} className="modal-alert" />
        <div className="approval-summary"><div><Text type="secondary">运输商</Text><div><strong>{selected.carrier}</strong> · {selected.biztype}</div></div><div><Text type="secondary">变更范围</Text><div>{selected.scope === 'project' ? '共享项目代码' : '单个运输商'}</div></div><div><Text type="secondary">变更文件</Text><ul>{selected.changed_files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div><div><Text type="secondary">方案摘要</Text><p>{selected.summary}</p></div></div>
        <label className="form-label">{decision === 'approve' ? '审批意见（选填）' : '拒绝原因（必填）'}</label>
        <TextArea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} showCount placeholder={decision === 'approve' ? '记录审批依据，便于审计追踪' : '请说明拒绝原因，以便后续修复和审查'} />
      </>}
    </Modal>
  </div>;
}