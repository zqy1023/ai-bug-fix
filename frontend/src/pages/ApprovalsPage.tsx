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
type ManualAction = 'retry_locate' | 'continue_repair' | 'mark_done' | 'terminate';

const manualActionLabels: Record<ManualAction, string> = {
  retry_locate: '重新定位',
  continue_repair: '批准 AI 继续修复',
  mark_done: '人工处理完毕',
  terminate: '终止自动处理',
};

const manualActionDescriptions: Record<ManualAction, string> = {
  retry_locate: '工作流会从根因定位节点继续。已有失败记录和 Agent 事件会保留。',
  continue_repair: '这会允许流程进入修复链路，但不会放宽文件权限，也不会跳过修改门禁、问题验证、回归和审查。',
  mark_done: '记录人工已在线下处理完毕。该操作不会伪造自动修复成功，也不会自动修改代码。',
  terminate: '记录人工决定终止该任务的自动修复。',
};

export function ApprovalsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [selected, setSelected] = useState<ApprovalItem | null>(null);
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve');
  const [reason, setReason] = useState('');
  const [manualSelected, setManualSelected] = useState<ManualIntervention | null>(null);
  const [manualDecision, setManualDecision] = useState<ManualAction>('retry_locate');
  const [manualReason, setManualReason] = useState('');

  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000 });
  const manual = useQuery({ queryKey: ['manual-interventions'], queryFn: api.manualInterventions, refetchInterval: 15000 });

  const refreshQueues = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['approvals'] }),
      queryClient.invalidateQueries({ queryKey: ['manual-interventions'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      queryClient.invalidateQueries({ queryKey: ['runs'] }),
    ]);
  };

  const mutation = useMutation({
    mutationFn: () => api.decideApproval(selected!.approval_id, decision, reason.trim()),
    onSuccess: async () => {
      message.success(decision === 'approve' ? '审批已提交' : '已提交拒绝决定');
      setSelected(null);
      setReason('');
      await refreshQueues();
    },
    onError: (error) => message.error('审批提交失败：' + (error as Error).message),
  });

  const manualMutation = useMutation({
    mutationFn: () => api.decideManualIntervention(manualSelected!.intervention_id, manualDecision, manualReason.trim()),
    onSuccess: async (result) => {
      message.success(result.status === 'resolved' ? '人工处置结果已记录' : '人工处置决定已提交，工作流正在恢复');
      setManualSelected(null);
      setManualReason('');
      await refreshQueues();
    },
    onError: (error) => message.error('人工处置提交失败：' + (error as Error).message),
  });

  const openManualAction = (row: ManualIntervention, action: ManualAction) => {
    setManualSelected(row);
    setManualDecision(action);
    setManualReason('');
  };

  const columns = [
    {
      title: '运输商',
      dataIndex: 'carrier',
      key: 'carrier',
      render: (v: string, row: ApprovalItem) => <div><strong>{v}</strong><div className="secondary-small">{row.biztype}</div></div>,
    },
    {
      title: '变更范围',
      dataIndex: 'scope',
      key: 'scope',
      render: (v: string) => <Tag color={v === 'project' ? 'purple' : 'blue'}>{v === 'project' ? '共享层变更' : '运输商内变更'}</Tag>,
    },
    { title: '修复摘要', dataIndex: 'summary', key: 'summary', width: '34%' },
    { title: '变更文件', dataIndex: 'changed_files', key: 'changed_files', render: (v: string[]) => (v?.length ?? 0) + ' 个文件' },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—',
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, row: ApprovalItem) => <Space wrap>
        <Button size="small" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id) + '?task_id=' + encodeURIComponent(row.task_id))}>查看执行证据</Button>
        <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => { setSelected(row); setDecision('approve'); setReason(''); }}>审批</Button>
        <Button size="small" danger icon={<CloseOutlined />} onClick={() => { setSelected(row); setDecision('reject'); setReason(''); }}>拒绝</Button>
      </Space>,
    },
  ];

  const manualColumns = [
    {
      title: '运输商',
      dataIndex: 'carrier',
      key: 'carrier',
      render: (v: string, row: ManualIntervention) => <div><strong>{v || '未知运输商'}</strong><div className="secondary-small">{row.biztype}</div></div>,
    },
    {
      title: '触发节点',
      dataIndex: 'step',
      key: 'step',
      render: (v: string) => ({ locate_agent: '根因定位', review_agent: '变更审查', prepare_evidence: '证据准备', repair_agent: 'AI 修复', finalize_execution: '执行收尾' } as Record<string, string>)[v] ?? v,
    },
    { title: '介入原因', dataIndex: 'summary', key: 'summary', width: '34%' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v: ManualIntervention['status']) => <Tag color={v === 'waiting_decision' ? 'gold' : 'default'}>{v === 'waiting_decision' ? '等待人工决定' : '等待处置登记'}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—',
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, row: ManualIntervention) => {
        const options = row.decision_options ?? [];
        return <Space wrap>
          <Button size="small" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id) + '/tasks/' + encodeURIComponent(row.task_id))}>查看证据</Button>
          {options.includes('RETRY_LOCATE') && <Button size="small" onClick={() => openManualAction(row, 'retry_locate')}>重新定位</Button>}
          {options.includes('CONTINUE_REPAIR') && <Button size="small" type="primary" onClick={() => openManualAction(row, 'continue_repair')}>批准 AI 继续修复</Button>}
          {options.includes('MARK_DONE') && <Button size="small" onClick={() => openManualAction(row, 'mark_done')}>人工处理完毕</Button>}
          {options.includes('TERMINATE') && <Button size="small" danger onClick={() => openManualAction(row, 'terminate')}>终止自动处理</Button>}
        </Space>;
      },
    },
  ];

  const manualReasonRequired = manualDecision !== 'retry_locate';
  return <div className="page-stack">
    <div className="page-heading">
      <div>
        <Title level={3}>人工审批与介入</Title>
        <Text type="secondary">方案审批只授权当前变更；人工介入可重新定位、在证据充分时批准继续修复、记录线下处理完毕或终止。</Text>
      </div>
      <Button icon={<ReloadOutlined />} onClick={() => { void approvals.refetch(); void manual.refetch(); }} loading={approvals.isFetching || manual.isFetching}>刷新</Button>
    </div>
    {approvals.isError && <Alert type="error" showIcon message="审批列表加载失败" description={(approvals.error as Error).message} action={<Button type="link" onClick={() => approvals.refetch()}>重试</Button>} />}
    <Tabs items={[
      {
        key: 'approvals',
        label: '待审批方案（' + (approvals.data?.length ?? 0) + '）',
        children: <Card className="content-card" title="待审批变更方案">
          <Alert type="info" showIcon message="批准不代表跳过验证" description="审批只允许当前方案进入后续流程。共享层方案仍须经过 apply_plan、变更门禁、问题验证、回归与审查；不会自动合并。" style={{ marginBottom: 16 }} />
          <Table rowKey="approval_id" loading={approvals.isLoading} columns={columns} dataSource={approvals.data ?? []} pagination={{ pageSize: 10, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '当前没有待处理审批' }} />
        </Card>,
      },
      {
        key: 'manual',
        label: '人工介入（' + (manual.data?.length ?? 0) + '）',
        children: <Card className="content-card" title="需要人工决定的任务">
          <Alert type="info" showIcon message="人工介入不等于变更审批" description="挂起中的定位任务可以选择重新定位；只有保留完整根因、源码证据和合法候选文件时，才会开放“批准 AI 继续修复”。历史终态任务无法原地续跑，只能登记人工处理完毕或终止。" style={{ marginBottom: 16 }} />
          {manual.isError && <Alert type="error" showIcon message="人工介入队列加载失败" description={(manual.error as Error).message} />}
          <Table rowKey="intervention_id" loading={manual.isLoading} dataSource={manual.data ?? []} pagination={{ pageSize: 10 }} locale={{ emptyText: '当前没有待人工介入任务' }} columns={manualColumns} />
        </Card>,
      },
    ]} />

    <Modal
      title={decision === 'approve' ? '确认批准变更方案' : '确认拒绝变更方案'}
      open={Boolean(selected)}
      onCancel={() => !mutation.isPending && setSelected(null)}
      onOk={() => mutation.mutate()}
      confirmLoading={mutation.isPending}
      okText={decision === 'approve' ? '确认批准' : '确认拒绝'}
      okButtonProps={{ danger: decision === 'reject', disabled: decision === 'reject' && !reason.trim() }}
      cancelText="返回检查"
      destroyOnClose
    >
      {selected && <>
        <Alert type={decision === 'approve' ? 'warning' : 'error'} showIcon message={decision === 'approve' ? '批准不代表跳过验证或自动合并' : '拒绝后工作流按既定重试与终止规则处理'} className="modal-alert" />
        <div className="approval-summary">
          <div><Text type="secondary">运输商 / 业务类型</Text><div><strong>{selected.carrier}</strong> · {selected.biztype}</div></div>
          <div><Text type="secondary">变更范围</Text><div>{selected.scope === 'project' ? '共享项目代码（高风险，确认影响范围）' : '单个运输商范围'}</div></div>
          <div><Text type="secondary">来源执行 / 任务</Text><div><code>{selected.run_id}</code><div><code>{selected.task_id}</code></div><Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate('/runs/' + encodeURIComponent(selected.run_id) + '/tasks/' + encodeURIComponent(selected.task_id))}>打开任务详情，检查根因、节点与验证报告</Button></div></div>
          <div><Text type="secondary">变更文件（{selected.changed_files.length}）</Text><ul>{selected.changed_files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div>
          <div><Text type="secondary">方案摘要</Text><p>{selected.summary}</p></div>
          {selected.expires_at && <div><Text type="secondary">审批截止时间</Text><div>{dayjs(selected.expires_at).isValid() ? dayjs(selected.expires_at).format('YYYY-MM-DD HH:mm:ss') : selected.expires_at}</div></div>}
        </div>
        <label className="form-label">{decision === 'approve' ? '审批意见（选填）' : '拒绝原因（必填）'}</label>
        <TextArea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} showCount placeholder={decision === 'approve' ? '记录审批依据，便于审计追踪' : '请说明拒绝原因，以便后续修复和审查'} />
      </>}
    </Modal>

    <Modal
      title={manualSelected ? manualActionLabels[manualDecision] : '人工处置'}
      open={Boolean(manualSelected)}
      onCancel={() => !manualMutation.isPending && setManualSelected(null)}
      onOk={() => manualMutation.mutate()}
      confirmLoading={manualMutation.isPending}
      okText={manualActionLabels[manualDecision]}
      okButtonProps={{ danger: manualDecision === 'terminate', disabled: manualReasonRequired && !manualReason.trim() }}
      cancelText="返回检查"
      destroyOnClose
      width={720}
    >
      {manualSelected && <>
        <Alert
          type={manualDecision === 'continue_repair' ? 'warning' : manualDecision === 'terminate' ? 'error' : 'info'}
          showIcon
          message={manualActionLabels[manualDecision]}
          description={manualActionDescriptions[manualDecision]}
          style={{ marginBottom: 16 }}
        />
        <div className="approval-summary">
          <div><Text type="secondary">运输商 / 业务类型</Text><div><strong>{manualSelected.carrier || '未知运输商'}</strong> · {manualSelected.biztype || '未知业务类型'}</div></div>
          <div><Text type="secondary">执行 / 任务</Text><div><code>{manualSelected.run_id}</code><div><code>{manualSelected.task_id}</code></div></div></div>
          <div><Text type="secondary">介入原因</Text><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{manualSelected.summary}</Paragraph></div>
          {manualSelected.root_cause && <div><Text type="secondary">当前根因结论</Text><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{manualSelected.root_cause}</Paragraph></div>}
          <div><Text type="secondary">候选文件（{manualSelected.candidate_files?.length ?? 0}）</Text>
            {manualSelected.candidate_files?.length
              ? <ul>{manualSelected.candidate_files.map((file) => <li key={file}><code>{file}</code></li>)}</ul>
              : <Paragraph type="secondary">没有可确认的候选文件；系统不会因人工操作自动扩大写入权限。</Paragraph>}
          </div>
        </div>
        <label className="form-label">{manualReasonRequired ? '处置说明（必填）' : '重新定位说明（选填）'}</label>
        <TextArea
          rows={3}
          value={manualReason}
          onChange={(e) => setManualReason(e.target.value)}
          maxLength={1000}
          showCount
          placeholder={manualDecision === 'continue_repair' ? '说明为何确认现有证据足以继续修复' : manualDecision === 'mark_done' ? '记录人工如何处理及必要的验证证据' : manualDecision === 'terminate' ? '说明终止自动修复的原因' : '可以补充人工观察到的线索'}
        />
      </>}
    </Modal>
  </div>;
}
