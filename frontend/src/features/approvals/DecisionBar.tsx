import { useState } from 'react';
import { App, Button, Input } from 'antd';
import { CheckOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ApprovalItem } from '../../lib/types';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

const { TextArea } = Input;

/** 决策栏：驳回必须填写原因并二次确认；批准保留分支供人工合并。 */
export function DecisionBar({ item, onDecided }: { item: ApprovalItem; onDecided: () => void }) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<'approve' | 'reject' | null>(null);

  const decide = useMutation({
    mutationFn: (decision: 'approve' | 'reject') => api.decideApproval(item.approval_id, decision, reason.trim()),
    onSuccess: async (_result, decision) => {
      message.success(decision === 'approve' ? '已批准，交付分支保留待人工合并' : '已驳回，系统会删除分支并回到修复链路');
      setReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.approvals }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runsRoot }),
        queryClient.invalidateQueries({ queryKey: queryKeys.run(item.run_id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.board(item.run_id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.task(item.run_id, item.task_id) }),
      ]);
      onDecided();
    },
    onError: (error) => message.error(error instanceof ApiError ? error.message : '提交审批失败'),
  });

  const run = (decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !reason.trim()) {
      message.warning('驳回必须填写原因');
      return;
    }
    setPending(decision);
    modal.confirm({
      title: decision === 'approve' ? '批准这次交付？' : '驳回并删除交付分支？',
      content: decision === 'approve'
        ? '将保留本次交付分支供人工合并，并结束这一次人工审批。'
        : '将删除已产出的交付分支，任务回到修复链路重新尝试。该操作会写入控制审计。',
      okText: decision === 'approve' ? '确认批准' : '确认驳回',
      okButtonProps: { danger: decision === 'reject' },
      cancelText: '返回',
      onOk: async () => {
        await decide.mutateAsync(decision).catch(() => undefined);
        setPending(null);
      },
      onCancel: () => setPending(null),
    });
  };

  return (
    <footer className="decision-bar">
      <div className="decision-reason">
        <label htmlFor="decision-reason">审批说明{/* 驳回时必填 */}</label>
        <TextArea
          id="decision-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          maxLength={2000}
          showCount
          placeholder="记录批准理由或风险备注；驳回时必须说明需要重新修复的原因"
        />
      </div>
      <div className="decision-actions">
        <Button
          danger
          icon={<StopOutlined />}
          disabled={!reason.trim()}
          loading={decide.isPending && pending === 'reject'}
          onClick={() => run('reject')}
        >
          驳回并回修
        </Button>
        <Button
          type="primary"
          icon={<CheckOutlined />}
          loading={decide.isPending && pending === 'approve'}
          onClick={() => run('approve')}
        >
          批准，保留分支
        </Button>
      </div>
    </footer>
  );
}
