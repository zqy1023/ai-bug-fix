import { useState } from 'react';
import { App, Button, Checkbox, Modal } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { AGENT_STEPS, WRITE_STEPS } from './stages';

export function StepActionBar({
  runId, taskId, stepName, stepStatus, runActive, approvalPending = false,
}: {
  runId: string; taskId: string; stepName: string; stepStatus: string;
  runActive: boolean; approvalPending?: boolean;
}) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [failure, setFailure] = useState('');
  const risky = WRITE_STEPS.has(stepName);
  const blockedByStep = stepName === 'request_approval' || stepName === 'retry_prepare';

  const mutation = useMutation({
    mutationFn: () => api.runStep(runId, taskId, stepName, risky ? confirmed : false),
    onSuccess: async () => {
      message.success(`${stepName} 已开始执行，步骤状态将实时更新`);
      setOpen(false);
      setConfirmed(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.task(runId, taskId) });
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

  if (blockedByStep || approvalPending) {
    return (
      <div className="step-action-bar is-blocked">
        {blockedByStep
          ? '审批必须在“交付审批”页完成，单步执行不提供该入口。'
          : '该任务仍有待处理交付审批，不能通过单步执行绕过；请先在交付审批页完成或恢复决策。'}
      </div>
    );
  }

  return (
    <div className="step-action-bar">
      <div className="step-action-copy">
        <strong>{stepStatus === 'failed' ? '失败节点重试' : '单步执行'}</strong>
        <span>基于本次 Run 的已落盘记录重建输入，前置步骤必须成功；只重跑当前节点，不会重跑整轮。</span>
        {runActive && <span className="action-warning">执行仍在活跃状态，后端会拒绝单步写入。</span>}
        {failure && <span className="action-failure">{failure}</span>}
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
        {risky && (
          <div className="confirm-block">
            <Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}>
              我确认该步骤可能产生外部写入（工作区、分支或修复资格）
            </Checkbox>
          </div>
        )}
        <p className="confirm-note">执行期间请勿同时操作整轮执行；活跃的 run 会被后端拒绝。</p>
      </Modal>
    </div>
  );
}
