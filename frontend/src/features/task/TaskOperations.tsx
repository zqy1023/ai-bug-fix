import type { FC } from 'react';
import { App, Button, Tooltip } from 'antd';
import { ArrowRightOutlined, ReloadOutlined, FileSearchOutlined, WarningOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { taskFailure, taskHasFailed, taskIsActive, taskState } from '../../lib/lifecycle';
import type { TaskDetailResponse } from '../../lib/types';
import { Banner } from '../../components/ui';
import styles from './TaskOperations.module.css';

interface TaskOperationsProps {
  detail: TaskDetailResponse;
  onInspect: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}

export const TaskOperations: FC<TaskOperationsProps> = ({ detail, onInspect, onRefresh, refreshing }) => {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const client = useQueryClient();
  const failed = taskHasFailed(detail.task);
  const active = taskIsActive(detail.task);
  const reason = taskFailure(detail);
  const retryAvailability = detail.actions?.retry;
  const current = detail.task.lifecycle?.current_step || detail.task.current_step;
  const currentTitle = detail.task.steps.find((step) => step.name === current)?.title || current;
  const retry = useMutation({
    mutationFn: () => api.retryTask(detail.run_id, detail.task.task_id),
    onSuccess: async (response) => {
      message.success(response.message);
      if (response.run_id && response.run_id !== detail.run_id) navigate(`/runs/${encodeURIComponent(response.run_id)}/tasks/${encodeURIComponent(detail.task.task_id)}`);
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.task(detail.run_id, detail.task.task_id) }),
        client.invalidateQueries({ queryKey: queryKeys.run(detail.run_id) }),
        client.invalidateQueries({ queryKey: queryKeys.board(detail.run_id) }),
        client.invalidateQueries({ queryKey: queryKeys.runsRoot }),
        client.invalidateQueries({ queryKey: queryKeys.dashboard }),
      ]);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : '重试提交失败'),
  });

  return (
    <section className={`${styles.card}${failed ? ` ${styles.cardFailed}` : ''}`} aria-label="任务状态与操作">
      <div className={styles.head}>
        <div className={styles.identity}>

          <strong>{detail.task.approval_pending ? '本地候选，等待审批后正式交付' : detail.delivery?.phase === 'published' ? '正式交付已完成' : active ? currentTitle || '正在分派任务' : taskState(detail.task) === 'BLOCKED' ? '验证受阻' : failed ? '需要处理' : '执行状态已同步'}</strong>
          {(detail.task.attempt ?? 0) > 0 && <span>第 {detail.task.attempt} 次尝试</span>}
        </div>
        <div className={styles.buttons}>
          <Tooltip title="重新获取任务状态"><Button aria-label="刷新任务" icon={<ReloadOutlined spin={refreshing} />} onClick={onRefresh} /></Tooltip>
          <Button icon={<FileSearchOutlined />} onClick={onInspect}>{failed ? '定位失败节点' : '执行记录'}</Button>
          {retryAvailability?.enabled ? (
            <Button type="primary" icon={<ReloadOutlined />} loading={retry.isPending} onClick={() => retry.mutate()}>{['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(taskState(detail.task)) ? '重新调查此任务' : '重试此任务'}</Button>
          ) : retryAvailability?.reason ? (
            // 不允许重试时给出禁用入口和原因，避免用户点出必然失败的操作。
            <Tooltip title={retryAvailability.reason}>
              <span className={styles.disabledAction}><Button disabled>暂不可重试</Button></span>
            </Tooltip>
          ) : null}
          {detail.task.approval_pending && <Button type="primary" onClick={() => navigate(`/approvals?task=${encodeURIComponent(detail.task.task_id)}`)}>处理审批 <ArrowRightOutlined /></Button>}
          {detail.run_error?.status && detail.run_error.error_type !== 'WORKFLOW_FAILED' && (
            <Button onClick={() => navigate(`/runs/${encodeURIComponent(detail.run_id)}`)}>处理批次异常 <ArrowRightOutlined /></Button>
          )}
        </div>
      </div>
      {retryAvailability?.reason && !retryAvailability.enabled && !active && (
        <p className={styles.permission}>暂不可重试：{retryAvailability.reason}</p>
      )}
      {reason && !active && <details className={styles.more}><summary>查看任务停止原因</summary><p className={styles.reason}>{reason === 'ANALYSIS_ONLY' ? '本轮仅输出分析报告，未确认代码修复。' : reason}</p></details>}
      {detail.run_error?.message && detail.run_error.error_type !== 'WORKFLOW_FAILED' && (
        <div className={styles.runErrorWrap} role="alert">
          <Banner tone="warn" icon={<WarningOutlined />}><strong>批次执行异常</strong> · {detail.run_error.message}</Banner>
        </div>
      )}
      <details className={styles.more}><summary>状态与操作说明</summary>
      <div className={styles.footer}>
        <span>{active ? '任务正在执行' : detail.task.approval_pending ? '等待人工决策' : '任务已停止自动推进'}</span>
        <span>{detail.worker_active ? '本轮工作流仍在运行' : detail.run_status === 'paused' ? '本轮已暂停' : '本轮没有活动工作线程'}</span>
        {!active && detail.task.lifecycle?.next_action && <span>{detail.task.lifecycle.next_action}</span>}
      </div>
      </details>
    </section>
  );
};
