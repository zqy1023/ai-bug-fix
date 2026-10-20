import { useMemo, useState } from 'react';
import { App, Button, Input, Select } from 'antd';
import { CheckOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ApprovalItem, ApprovalRetryTarget } from '../../lib/types';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

const { TextArea } = Input;

type RejectReasonCode = 'root_cause' | 'code_change' | 'validation' | 'regression' | 'review' | 'delivery' | 'other';

const REJECTION_REASONS: Array<{
  value: RejectReasonCode;
  label: string;
  retryTarget: ApprovalRetryTarget;
  help: string;
}> = [
  {
    value: 'root_cause',
    label: '根因定位不准确',
    retryTarget: 'locate_agent',
    help: '重新运行根因定位，再根据新的定位结论进入修复链路。',
  },
  {
    value: 'code_change',
    label: '代码修改有误或不完整',
    retryTarget: 'repair_agent',
    help: '恢复目标 carrier 的干净基线后重新执行代码修复。',
  },
  {
    value: 'validation',
    label: '问题验证不足',
    retryTarget: 'validate_changes',
    help: '保留当前候选代码，重新执行变更门禁和问题验证。',
  },
  {
    value: 'regression',
    label: '回归验证不足',
    retryTarget: 'run_regression',
    help: '保留当前候选代码，重新执行回归验证。',
  },
  {
    value: 'review',
    label: '需要重新进行 AI 审查',
    retryTarget: 'review_agent',
    help: '保留当前候选代码和验证结果，重新运行 AI 审查。',
  },
  {
    value: 'delivery',
    label: '交付分支或提交信息有误',
    retryTarget: 'prepare_delivery',
    help: '保留已审查代码和审查结论，重新创建交付分支。',
  },
  {
    value: 'other',
    label: '其他（自定义原因）',
    retryTarget: 'repair_agent',
    help: '请补充原因，并明确选择需要重新执行的阶段。',
  },
];

const TARGET_LABELS: Record<ApprovalRetryTarget, string> = {
  locate_agent: '根因定位',
  repair_agent: '代码修复',
  validate_changes: '变更门禁与问题验证',
  run_regression: '回归验证',
  review_agent: 'AI 审查',
  prepare_delivery: 'Git 分支交付',
};

type DecisionInput = {
  decision: 'approve' | 'reject';
  reason: string;
  retryTarget: ApprovalRetryTarget;
};

/** 驳回原因映射到确定的重跑阶段；自定义原因要求显式选择回退节点。 */
export function DecisionBar({ item, onDecided }: { item: ApprovalItem; onDecided: () => void }) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const [reasonCode, setReasonCode] = useState<RejectReasonCode>('code_change');
  const [retryTargetOverride, setRetryTargetOverride] = useState<ApprovalRetryTarget>('repair_agent');
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<'approve' | 'reject' | null>(null);

  const selectedReason = useMemo(
    () => REJECTION_REASONS.find((option) => option.value === reasonCode) ?? REJECTION_REASONS[1],
    [reasonCode],
  );
  const retryTarget = reasonCode === 'other' ? retryTargetOverride : selectedReason.retryTarget;
  const rejectReason = reasonCode === 'other'
    ? reason.trim()
    : `${selectedReason.label}${reason.trim() ? `；补充说明：${reason.trim()}` : ''}`;

  const decide = useMutation({
    mutationFn: ({ decision, reason: decisionReason, retryTarget: target }: DecisionInput) =>
      api.decideApproval(item.approval_id, decision, decisionReason, target),
    onSuccess: async (_result, variables) => {
      if (variables.decision === 'approve') {
        message.success('已批准，交付分支保留供你手动合并');
      } else {
        message.success(`已驳回，将从「${TARGET_LABELS[variables.retryTarget]}」阶段重新执行`);
      }
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
    if (decision === 'reject' && reasonCode === 'other' && !reason.trim()) {
      message.warning('选择“其他”时必须单独填写驳回原因');
      return;
    }
    const submission: DecisionInput = decision === 'approve'
      ? { decision, reason: reason.trim(), retryTarget: 'repair_agent' }
      : { decision, reason: rejectReason, retryTarget };

    setPending(decision);
    modal.confirm({
      title: decision === 'approve' ? '批准这次交付？' : '驳回并删除交付分支？',
      content: decision === 'approve'
        ? '将保留本次交付分支供你手动检查和合并；系统不会自动合并。'
        : `系统将先清理当前交付分支，再从「${TARGET_LABELS[retryTarget]}」阶段重新执行。当前尝试次数达到上限时，任务会按工作流规则终止。`,
      okText: decision === 'approve' ? '确认批准' : '确认驳回并重跑',
      okButtonProps: { danger: decision === 'reject' },
      cancelText: '返回',
      onOk: async () => {
        await decide.mutateAsync(submission).catch(() => undefined);
        setPending(null);
      },
      onCancel: () => setPending(null),
    });
  };

  return (
    <footer className="decision-bar">
      <div className="decision-reason">
        <div className="decision-reject-field">
          <label htmlFor="approval-reject-reason">驳回原因</label>
          <Select
            id="approval-reject-reason"
            aria-label="驳回原因"
            value={reasonCode}
            options={REJECTION_REASONS.map((option) => ({ value: option.value, label: option.label }))}
            onChange={(value: RejectReasonCode) => setReasonCode(value)}
            popupMatchSelectWidth={false}
          />
        </div>

        <div className="decision-retry-route">
          <div>
            <span>重新执行阶段</span>
            <strong>{TARGET_LABELS[retryTarget]}</strong>
          </div>
          {reasonCode === 'other' ? (
            <Select
              aria-label="自定义原因的回退阶段"
              value={retryTargetOverride}
              options={Object.entries(TARGET_LABELS).map(([value, label]) => ({ value, label }))}
              onChange={(value: ApprovalRetryTarget) => setRetryTargetOverride(value)}
            />
          ) : (
            <p>{selectedReason.help}</p>
          )}
        </div>

        <div className="decision-reason-note">
          <label htmlFor="decision-reason">
            {reasonCode === 'other' ? '驳回说明（必填）' : '补充说明（可选）'}
          </label>
          <TextArea
            id="decision-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            maxLength={1000}
            showCount
            placeholder={reasonCode === 'other'
              ? '说明其他驳回原因，以及希望系统如何调整'
              : '补充具体文件、问题或验收要求；预设原因会自动写入审计'}
          />
        </div>
      </div>
      <div className="decision-actions">
        <Button
          danger
          icon={<StopOutlined />}
          disabled={reasonCode === 'other' && !reason.trim()}
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
