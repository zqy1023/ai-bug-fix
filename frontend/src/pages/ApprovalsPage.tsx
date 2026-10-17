import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App as AntApp, Button, Card, Descriptions, Empty, Input, Modal, Paragraph, Space, Table, Tag, Typography } from 'antd';
import { CheckOutlined, ClockCircleOutlined, ReloadOutlined, SafetyCertificateOutlined, StopOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { ApprovalItem } from '../lib/types';

const { Title, Text } = Typography;
const { TextArea } = Input;
function formatDate(value?: string | null) {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : value;
}

export function ApprovalsPage() {
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<ApprovalItem | null>(null);
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve');
  const [reason, setReason] = useState('');
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000 });

  const decide = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error('请先选择待审批交付');
      if (decision === 'reject' && !reason.trim()) throw new Error('拒绝时必须填写理由');
      return api.decideApproval(selected.approval_id, decision, reason.trim());
    },
    onSuccess: async () => {
      const runId = selected?.run_id;
      const taskId = selected?.task_id;
      message.success(decision === 'approve' ? '已批准交付审批' : '已拒绝交付审批');
      setSelected(null);
      setReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['approvals'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
        queryClient.invalidateQueries({ queryKey: ['runs'] }),
        ...(runId ? [queryClient.invalidateQueries({ queryKey: ['run', runId] })] : []),
        ...(runId && taskId ? [queryClient.invalidateQueries({ queryKey: ['task', runId, taskId] })] : []),
      ]);
    },
    onError: (error) => message.error('提交审批失败：' + (error as Error).message),
  });

  const openDecision = (item: ApprovalItem, next: 'approve' | 'reject') => {
    setSelected(item);
    setDecision(next);
    setReason('');
  };

  const columns = [
    {
      title: '待审批交付',
      key: 'delivery',
      render: (_: unknown, item: ApprovalItem) => (
        <button className="table-identity approval-identity" onClick={() => setSelected(item)}>
          <span className="approval-row-mark"><SafetyCertificateOutlined /></span>
          <span><strong>{item.carrier} <span className="identity-separator">/</span> {item.biztype}</strong><small>{item.summary || item.task_id}</small></span>
        </button>
      ),
    },
    {
      title: '执行轮次',
      dataIndex: 'run_id',
      key: 'run_id',
      render: (value: string, item: ApprovalItem) => (
        <div className="approval-run-id">
          <a onClick={() => navigate('/runs/' + encodeURIComponent(value))}>{value}</a>
          <small>任务 {item.task_id}</small>
        </div>
      ),
    },
    { title: '变更范围', dataIndex: 'scope', key: 'scope', render: (value: ApprovalItem['scope']) => <Tag color={value === 'carrier' ? 'blue' : 'orange'}>{value === 'carrier' ? '运输商范围' : '项目 / 共享范围'}</Tag> },
    { title: '交付状态', dataIndex: 'status', key: 'status', render: (value: ApprovalItem['status']) => <StatusTag status={value} /> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: formatDate },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, item: ApprovalItem) => (
        <Space size={4}>
          <Button type="link" onClick={() => setSelected(item)}>审查</Button>
          <Button type="primary" size="small" icon={<CheckOutlined />} onClick={() => openDecision(item, 'approve')}>批准</Button>
          <Button danger type="text" size="small" icon={<StopOutlined />} onClick={() => openDecision(item, 'reject')}>拒绝</Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="page-stack approvals-page">
      <div className="page-heading">
        <div>
          <Title level={3}>最终审批</Title>
          <Text type="secondary">只处理已经完成验证、回归、审查并准备交付的方案。批准或拒绝将由服务端做状态、权限和审计校验。</Text>
        </div>
        <Button icon={<ReloadOutlined />} loading={approvals.isFetching} onClick={() => approvals.refetch()}>刷新队列</Button>
      </div>

      <div className="approval-policy-strip">
        <span className="policy-icon"><SafetyCertificateOutlined /></span>
        <div><strong>审批只发生在交付门禁</strong><span>根因定位、修复和回归过程不在这里手动接管；证据不足或共享层风险会按工作流规则独立收敛。</span></div>
      </div>

      {approvals.isError && <Alert type="error" showIcon message="无法读取审批队列" description={(approvals.error as Error).message} action={<Button type="link" onClick={() => approvals.refetch()}>重试</Button>} />}
      <div className="approval-queue-header">
        <div><Title level={4}>待决策交付</Title><Text type="secondary">当前用户有权处理的待审批项</Text></div>
        <span className="approval-count"><strong>{approvals.data?.length ?? 0}</strong> 项待处理</span>
      </div>

      <Card className="content-card approval-table-card">
        {approvals.isLoading ? <div className="approval-loading"><ClockCircleOutlined /><span>正在加载待审批交付…</span></div> : (
          <Table rowKey="approval_id" columns={columns} dataSource={approvals.data ?? []}
            pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => '共 ' + total + ' 项待审批' }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有待审批交付" /> }} scroll={{ x: 900 }} />
        )}
      </Card>

      <Modal
        title={decision === 'approve' ? '批准交付方案' : '拒绝交付方案'}
        open={Boolean(selected)}
        onCancel={() => { if (!decide.isPending) { setSelected(null); setReason(''); } }}
        onOk={() => decide.mutate()}
        confirmLoading={decide.isPending}
        okText={decision === 'approve' ? '确认批准' : '确认拒绝'}
        cancelText="返回"
        okButtonProps={{ danger: decision === 'reject', disabled: decision === 'reject' && !reason.trim() }}
        width={760}
        destroyOnClose
      >
        {selected && <div className="approval-review">
          <div className="approval-review-heading">
            <span className="approval-review-icon"><SafetyCertificateOutlined /></span>
            <div><strong>{selected.carrier} / {selected.biztype}</strong><span>{selected.summary || '等待最终交付审批'}</span></div>
          </div>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="执行轮次"><a onClick={() => navigate('/runs/' + encodeURIComponent(selected.run_id))}>{selected.run_id}</a></Descriptions.Item>
            <Descriptions.Item label="任务 ID">{selected.task_id}</Descriptions.Item>
            <Descriptions.Item label="变更范围">{selected.scope === 'carrier' ? '运输商目录' : '项目 / 共享范围'}</Descriptions.Item>
            <Descriptions.Item label="申请时间">{formatDate(selected.created_at)}</Descriptions.Item>
            <Descriptions.Item label="审批到期时间">{formatDate(selected.expires_at)}</Descriptions.Item>
            <Descriptions.Item label="变更文件数">{selected.changed_files?.length ?? 0}</Descriptions.Item>
          </Descriptions>

          {selected.change_plan && <div className="approval-plan-block">
            <div className="approval-section-title">方案说明</div>
            <Paragraph>{selected.change_plan.rationale || '未提供额外方案说明。'}</Paragraph>
            {selected.change_plan.requires_human_authorization && <Alert type="warning" showIcon message="此方案需要人工授权" description="请确认变更范围和影响后，再决定是否批准。" />}
            {!!selected.change_plan.edits?.length && <div className="approval-file-list">{selected.change_plan.edits.map((edit) => (
              <div className="approval-file-row" key={edit.path}><code>{edit.path}</code><Tag>{edit.action}</Tag><Text type="secondary">{edit.note}</Text></div>
            ))}</div>}
          </div>}

          <div className="approval-plan-block">
            <div className="approval-section-title">交付变更文件</div>
            {selected.changed_files?.length ? <div className="approval-file-list">{selected.changed_files.map((path) => <div className="approval-file-row" key={path}><code>{path}</code></div>)}</div> : <Text type="secondary">后端未返回变更文件列表。请先进入执行详情确认交付信息。</Text>}
          </div>
          <div className="approval-reason-field">
            <label htmlFor="approval-reason">{decision === 'reject' ? '拒绝理由（必填）' : '审批说明（可选）'}</label>
            <TextArea id="approval-reason" value={reason} onChange={(event) => setReason(event.target.value)} rows={3} maxLength={2000} showCount placeholder={decision === 'reject' ? '说明不批准的原因，以便后续重试和审计' : '记录批准理由或风险备注'} />
          </div>
          <Text type="secondary" className="approval-warning">此操作会改变服务端审批状态并写入审计记录。它不等同于将代码合并到目标分支。</Text>
        </div>}
      </Modal>
    </div>
  );
}
