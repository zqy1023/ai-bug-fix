import type { FC } from 'react';
import { App, Button, Tooltip } from 'antd';
import { ArrowRightOutlined, ReloadOutlined, FileSearchOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { taskCanRetry, taskFailure, taskHasFailed, taskIsActive, taskState } from '../../lib/lifecycle';
import type { TaskDetailResponse } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';

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
  const current = detail.task.lifecycle?.current_step || detail.task.current_step;
  const currentTitle = detail.task.steps.find((step) => step.name === current)?.title || current;
  const retry = useMutation({
    mutationFn: () => api.retryTask(detail.run_id, detail.task.task_id),
    onSuccess: async (response) => {
      message.success(response.message);
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
    <section className={`task-operations ${failed ? 'is-failed' : ''}`} aria-label="任务状态与操作">
      <div className="task-operations-heading">
        <div className="task-operations-identity">
          <span className={`operation-signal ${active ? 'is-active' : ''}`} />
          <StatusBadge kind="task" value={taskState(detail.task)} />
          <strong>{active ? currentTitle || '正在分派任务' : failed ? '需要处理' : '执行状态已同步'}</strong>
          {(detail.task.attempt ?? 0) > 0 && <span>第 {detail.task.attempt} 次尝试</span>}
        </div>
        <div className="task-operations-buttons">
          <Tooltip title="重新获取任务状态"><Button aria-label="刷新任务" icon={<ReloadOutlined spin={refreshing} />} onClick={onRefresh} /></Tooltip>
          <Button icon={<FileSearchOutlined />} onClick={onInspect}>{failed ? '定位失败节点' : '执行记录'}</Button>
          {taskCanRetry(detail) && <Button type="primary" icon={<ReloadOutlined />} loading={retry.isPending} onClick={() => retry.mutate()}>重试此任务</Button>}
          {detail.task.approval_pending && <Button type="primary" onClick={() => navigate('/approvals')}>处理审批 <ArrowRightOutlined /></Button>}
          {detail.run_error?.status && detail.run_error.error_type !== 'WORKFLOW_FAILED' && (
            <Button onClick={() => navigate(`/runs/${encodeURIComponent(detail.run_id)}`)}>处理批次异常 <ArrowRightOutlined /></Button>
          )}
        </div>
      </div>
      {reason && !active && <p className="task-operations-reason" role={failed ? 'alert' : undefined}>{reason}</p>}
      {detail.run_error?.message && detail.run_error.error_type !== 'WORKFLOW_FAILED' && (
        <div className="task-run-error" role="alert"><strong>批次执行异常</strong><span>{detail.run_error.message}</span></div>
      )}
      <div className="task-operations-footer">
        <span>{active ? '任务正在执行' : detail.task.approval_pending ? '等待人工决策' : '任务已停止自动推进'}</span>
        <span>{detail.worker_active ? '本轮工作流仍在运行' : detail.run_status === 'paused' ? '本轮已暂停' : '本轮没有活动工作线程'}</span>
        {!active && detail.task.lifecycle?.next_action && <span>{detail.task.lifecycle.next_action}</span>}
      </div>
    </section>
  );
};
