import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App, Button, Input, Select } from 'antd';
import { CheckOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ApprovalItem, ApprovalRetryTarget } from '../../lib/types';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import styles from './DecisionBar.module.css';

const { TextArea } = Input;

type RejectReasonCode = 'root_cause' | 'code_change' | 'validation' | 'regression' | 'review' | 'delivery' | 'other';

const REJECTION_REASONS: Array<{
  value: RejectReasonCode;
  label: string;
  retryTarget?: ApprovalRetryTarget;
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
    retryTarget: 'review_agent',
    help: '保留当前候选代码，重新执行修复后代码审查及其内部验证。',
  },
  {
    value: 'regression',
    label: '回归验证不足',
    retryTarget: 'review_agent',
    help: '保留当前候选代码，重新执行修复后代码审查及其内部请求验证。',
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
    help: '保留已审查代码和审查结论，重新准备本地候选。',
  },
  {
    value: 'other',
    label: '其他（自定义原因）',
    help: '请补充原因，并明确选择需要重新执行的阶段。',
  },
];

const TARGET_LABELS: Record<ApprovalRetryTarget, string> = {
  locate_agent: '根因定位',
  repair_agent: '代码修复',
  review_agent: 'AI 审查',
  prepare_delivery: 'Git 分支交付',
};

const RECOVERY_LABELS: Record<string, string> = {
  verified: '已验证',
  unverified: '未验证',
  failed: '失败',
  not_executed: '未执行',
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
  const navigate = useNavigate();
  const [reasonCode, setReasonCode] = useState<RejectReasonCode | ''>('');
  const [retryTargetOverride, setRetryTargetOverride] = useState<ApprovalRetryTarget | ''>('');
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<'approve' | 'reject' | null>(null);

  const selectedReason = useMemo(
    () => REJECTION_REASONS.find((option) => option.value === reasonCode),
    [reasonCode],
  );
  const recovery = item.business_recovery;
  const recoveryWarning = recovery && recovery.status !== 'verified'
    ? `注意：实时业务验证为「${RECOVERY_LABELS[recovery.status] ?? recovery.status}」，批准只代表代码修复通过，不代表业务已恢复。${recovery.reason ? `（${recovery.reason}）` : ''}`
    : '';
  const retryTarget = reasonCode === 'other' ? retryTargetOverride : selectedReason?.retryTarget ?? '';
  const rejectReason = reasonCode === 'other'
    ? reason.trim()
    : selectedReason
      ? `${selectedReason.label}${reason.trim() ? `；补充说明：${reason.trim()}` : ''}`
      : '';

  const decide = useMutation({
    mutationFn: ({ decision, reason: decisionReason, retryTarget: target }: DecisionInput) =>
      api.decideApproval(item.approval_id, decision, decisionReason, target),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.approvals }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runsRoot }),
        queryClient.invalidateQueries({ queryKey: queryKeys.run(item.run_id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.board(item.run_id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.task(item.run_id, item.task_id) }),
      ]);
      setReason('');
      onDecided();
      if (variables.decision === 'approve') {
        message.success('批准请求已受理，后台将正式交付本次候选；请在任务详情查看交付结果');
        navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`);
      } else {
        // API only confirms that the async checkpoint resume was accepted. It cannot
        // guarantee cleanup or retry has already succeeded, so take the operator to live task status.
        message.info(`驳回请求已受理；后台将从「${TARGET_LABELS[variables.retryTarget]}」恢复。请以任务详情中的实际状态为准。`);
        navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`);
      }
    },
    onError: (error) => message.error(error instanceof ApiError ? error.message : '提交审批失败'),
  });

  const run = (decision: 'approve' | 'reject') => {
    if (pending || decide.isPending) return;
    if (decision === 'reject' && !reasonCode) {
      message.warning('请先选择驳回原因，确保任务回到正确的执行阶段');
      return;
    }
    if (decision === 'reject' && reasonCode === 'other' && !retryTargetOverride) {
      message.warning('选择“其他”时必须明确选择回退阶段');
      return;
    }
    if (decision === 'reject' && reasonCode === 'other' && !reason.trim()) {
      message.warning('选择“其他”时必须单独填写驳回原因');
      return;
    }
    const submission: DecisionInput = decision === 'approve'
      ? { decision, reason: reason.trim(), retryTarget: 'repair_agent' }
      : { decision, reason: rejectReason, retryTarget: retryTarget as ApprovalRetryTarget };

    setPending(decision);
    modal.confirm({
      title: decision === 'approve' ? '确认保留这次交付？' : '打回并撤回已交付分支？',
      content: decision === 'approve' ? (
        <>
          {recoveryWarning && <p style={{ color: '#d46b08', marginBottom: 8 }}>{recoveryWarning}</p>}
          <p>修复分支已交付；确认前会重新核验候选提交。保留后由人工合并，系统不会自动合并。</p>
        </>
      ) : `系统将撤回本次交付的修复分支，再从「${retryTarget ? TARGET_LABELS[retryTarget] : '未选择阶段'}」重新执行。当前尝试次数达到上限时，任务会按工作流规则终止。`,
      okText: decision === 'approve' ? '确认保留' : '确认打回并重跑',
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
    <footer className={styles.bar}>
      <details className={styles.options}>
      <summary>补充说明 / 设置驳回原因与回修阶段</summary>
      <div className={styles.row}>
        <div className={styles.field}>
          <label htmlFor="approval-reject-reason">驳回原因</label>
          <Select
            id="approval-reject-reason"
            aria-label="驳回原因"
            value={reasonCode || undefined}
            placeholder="请选择驳回原因"
            options={REJECTION_REASONS.map((option) => ({ value: option.value, label: option.label }))}
            onChange={(value: RejectReasonCode) => setReasonCode(value)}
            popupMatchSelectWidth={false}
          />
        </div>

        <div className={styles.route}>
          <div>
            <span>重新执行阶段</span>
            <strong>{retryTarget ? TARGET_LABELS[retryTarget] : '选择原因后确定回退阶段'}</strong>
          </div>
          {reasonCode === 'other' ? (
            <Select
              aria-label="自定义原因的回退阶段"
              value={retryTargetOverride || undefined}
              placeholder="请选择回退阶段"
              options={Object.entries(TARGET_LABELS).map(([value, label]) => ({ value, label }))}
              onChange={(value: ApprovalRetryTarget) => setRetryTargetOverride(value)}
            />
          ) : (
            <p>{selectedReason?.help || '请先选择驳回原因；系统将按预设映射恢复到对应节点。'}</p>
          )}
        </div>
      </div>

      <div className={styles.noteField}>
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
      </details>
      {!item.actions?.approve.enabled && <p className={styles.warning}>{item.actions?.approve.reason || '服务未提供操作许可，暂不能提交审批'}</p>}
      <div className={styles.actions}>
        <p>确认后保留已交付的修复分支<br /><span>系统不会自动合并</span></p>
        <Button
          danger
          aria-label="打回并撤回"
          icon={<StopOutlined />}
          disabled={Boolean(pending) || !item.actions?.reject.enabled || !reasonCode || (reasonCode === 'other' && (!retryTargetOverride || !reason.trim()))}
          loading={decide.isPending && pending === 'reject'}
          onClick={() => run('reject')}
        >
          打回并撤回
        </Button>
        <Button
          type="primary"
          aria-label="确认保留"
          icon={<CheckOutlined />}
          disabled={Boolean(pending) || !item.actions?.approve.enabled}
          title={item.actions?.approve.reason || (!item.actions ? '服务未提供操作许可' : undefined)}
          loading={decide.isPending && pending === 'approve'}
          onClick={() => run('approve')}
        >
          确认保留
        </Button>
      </div>
    </footer>
  );
}
