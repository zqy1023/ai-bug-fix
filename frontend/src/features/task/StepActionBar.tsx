import type { ActionAvailability } from '../../lib/types';
import { useState } from 'react';
import { App, Button, Checkbox, Modal } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { AGENT_STEPS } from './stages';
import styles from './StepActionBar.module.css';

export function StepActionBar({
  runId, taskId, stepName, stepStatus, runActive, approvalPending = false, availability, continuation,
}: {
  runId: string; taskId: string; stepName: string; stepStatus: string;
  runActive: boolean; approvalPending?: boolean; availability?: ActionAvailability; continuation?: ActionAvailability;
}) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [failure, setFailure] = useState('');
  const [continueRequested, setContinueRequested] = useState(false);
  const continueAfter = continueRequested && continuation?.enabled === true;
  const risky = availability?.requires_confirmation || continueAfter;
  const blockedByStep = stepName === 'request_approval' || stepName === 'retry_prepare';

  const mutation = useMutation({
    mutationFn: () => api.runStep(runId, taskId, stepName, risky ? confirmed : false, continueAfter),
    onSuccess: async () => {
      message.success(`${stepName} 已开始执行${continueAfter ? '，完成后将继续后续流程' : ''}`);
      setOpen(false);
      setConfirmed(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.task(runId, taskId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.step(runId, taskId, stepName) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.board(runId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.modelCalls(runId, taskId) });
    },
    onError: (error) => {
      const text = error instanceof ApiError ? `${error.code}：${error.message}` : '单步执行失败';
      setFailure(text);
      message.error(text);
    },
  });

  if (!availability || !availability.enabled || blockedByStep || approvalPending) {
    return (
      <div className={`${styles.bar} ${styles.blocked}`}>
        {availability?.reason || (!availability ? '正在确认操作权限；旧版服务不提供操作许可，请升级后端。' : blockedByStep
          ? '审批必须在“交付审批”页完成，单步执行不提供该入口。'
          : '该任务仍有待处理交付审批，不能通过单步执行绕过；请先在交付审批页完成或恢复决策。')}
      </div>
    );
  }

  return (
    <div className={styles.bar}>
      <div className={styles.copy}>
        <strong>{continueAfter ? '从此节点继续执行' : stepStatus === 'failed' ? '失败节点重试' : '仅执行当前节点'}</strong>
        <span>重跑所选节点，默认接续后续流程；不会从头重跑已完成节点，人工审批仍需单独处理。</span>
        {runActive && <span className={styles.warning}>执行仍在活跃状态，后端会拒绝单步写入。</span>}
        {failure && <span className={styles.failure}>{failure}</span>}
      </div>
      <Button type="primary" icon={<PlayCircleOutlined />} disabled={runActive} onClick={() => setOpen(true)}>
        {stepStatus === 'failed'
          ? AGENT_STEPS.has(stepName) ? '重试本 Agent' : '重试本步骤'
          : AGENT_STEPS.has(stepName) ? '重跑本 Agent' : '执行本步骤'}
      </Button>

      <Modal
        title={`执行 ${stepName}`}
        open={open}
        onCancel={() => { setOpen(false); setConfirmed(false); }}
        okText="确认执行"
        cancelText="取消"
        okButtonProps={{ disabled: risky && !confirmed, loading: mutation.isPending }}
        onOk={() => mutation.mutate()}
      >
        <p>将基于已落盘记录重建输入并执行该步骤。执行结果会覆盖当前步骤记录并写入控制审计。</p>
        <Checkbox checked={continueAfter} disabled={!continuation?.enabled} onChange={(event) => setContinueRequested(event.target.checked)}>完成后继续后续流程（直到审批、暂停或任务结束）</Checkbox>
        {continuation?.reason && <p className={styles.note}>{continuation.reason}</p>}
        {risky && (
          <div className={styles.confirmBlock}>
            <Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}>
              我确认该步骤可能产生外部写入（工作区、分支或修复资格）
            </Checkbox>
          </div>
        )}
        <p className={styles.note}>执行期间请勿同时操作整轮执行；活跃的 run 会被后端拒绝。</p>
      </Modal>
    </div>
  );
}
