import { App, Button, Tooltip } from 'antd';
import { CaretRightOutlined, PauseOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { RunDetail } from '../../lib/types';
import { formatTime } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';

/** 旧后端没有 identified_tasks 字段时，从 identify_carrier 输出回推识别数量。 */
function identifiedOf(run: RunDetail): number {
  if (typeof run.identified_tasks === 'number') return run.identified_tasks;
  const step = run.steps?.find((item) => item.name === 'identify_carrier' && item.status === 'succeeded');
  const tasks = step?.output?.tasks;
  if (Array.isArray(tasks)) return tasks.length;
  if (tasks && typeof tasks === 'object') return Object.keys(tasks).length;
  return 0;
}

export function RunHeader({ run }: { run: RunDetail }) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const identified = identifiedOf(run);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.run(run.run_id) });
    await queryClient.invalidateQueries({ queryKey: queryKeys.board(run.run_id) });
    await queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
  };

  const control = useMutation({
    mutationFn: async (action: 'pause' | 'resume' | 'cancel') => {
      if (action === 'pause') return api.pauseRun(run.run_id);
      if (action === 'resume') return api.resumeRun(run.run_id);
      return api.cancelRun(run.run_id);
    },
    onSuccess: async (result, action) => {
      message.success(result.message ?? (action === 'pause' ? '已请求暂停' : action === 'resume' ? '已提交恢复' : '已请求停止'));
      await invalidate();
    },
    onError: (error) => message.error(error instanceof ApiError ? error.message : '操作失败'),
  });

  const status = run.status;
  const canPause = ['running', 'queued', 'pause_requested'].includes(status);
  const canResume = ['paused', 'failed'].includes(status) && Boolean(run.checkpoint?.resumable);
  const canCancel = ['running', 'queued', 'paused', 'pause_requested'].includes(status);

  const confirmCancel = () => {
    modal.confirm({
      title: '停止这次执行？',
      content: '停止会在当前节点安全边界收尾，已产出的分支与记录不会回滚。',
      okText: '停止执行',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => control.mutateAsync('cancel'),
    });
  };

  return (
    <div className="run-header">
      <div className="run-header-main">
        <span className="run-header-kicker">执行工作区 · LangGraph 控制面</span>
        <div className="run-header-title">
          <h2 title={run.run_id}>run:{run.run_id}</h2>
          <StatusBadge kind="run" value={status} />
        </div>
        <div className="run-header-meta">
          <span>开始 {formatTime(run.started_at)}</span>
          <span>任务 {run.succeeded_tasks} 自动 / {run.waiting_tasks} 待审批 / {run.manual_tasks} 转人工 / {run.failed_tasks} 失败</span>
          {identified > 0 ? (
            <span>已派发 {run.dispatched_tasks ?? run.task_count}/{identified}</span>
          ) : (
            <span>共 {run.task_count} 个任务</span>
          )}
          {run.status === 'running' && (run.steps?.length ?? 0) === 0 && (
            <span className="run-phase">启动中：同步源码、拉取日志</span>
          )}
          {run.checkpoint?.node && <span>当前节点 {run.checkpoint.node}</span>}
        </div>
      </div>
      <div className="run-header-actions">
        <Tooltip title={canPause ? '在下一个节点边界暂停' : '当前状态不可暂停'}>
          <Button
            icon={<PauseOutlined />}
            disabled={!canPause}
            loading={control.isPending && control.variables === 'pause'}
            onClick={() => control.mutate('pause')}
          >暂停</Button>
        </Tooltip>
        <Tooltip title={canResume ? '从 checkpoint 恢复' : '没有可恢复的 checkpoint'}>
          <Button
            icon={<CaretRightOutlined />}
            disabled={!canResume}
            loading={control.isPending && control.variables === 'resume'}
            onClick={() => control.mutate('resume')}
          >恢复</Button>
        </Tooltip>
        <Button
          danger
          icon={<StopOutlined />}
          disabled={!canCancel}
          loading={control.isPending && control.variables === 'cancel'}
          onClick={confirmCancel}
        >停止</Button>
      </div>
      {(run.run_error?.message || run.run_report?.terminal_reason) && (
        <div className={'run-notice' + (run.run_error?.message ? ' is-error' : '')}>
          <strong>
            {run.run_error?.terminal_step || run.run_report?.terminal_step || '执行终态'}
          </strong>
          <span>{run.run_error?.message || run.run_report?.terminal_reason}</span>
          {(run.run_error?.next_action || run.run_report?.next_action) && (
            <em>下一步：{run.run_error?.next_action || run.run_report?.next_action}</em>
          )}
        </div>
      )}
    </div>
  );
}
