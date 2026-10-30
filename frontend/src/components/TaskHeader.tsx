import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { durationFrom, formatDuration } from '../lib/format';
import { taskStatus } from '../lib/status';
import { useLiveClock } from '../lib/hooks';
import type { TaskDetailResponse } from '../lib/types';
import { PageHeader, TaskStatusBadge, Tabs, useToast } from './ui';

export function TaskHeader({ detail, active }: { detail: TaskDetailResponse; active: string }) {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const task = detail.task;
  const meta = taskStatus(task.result, task.lifecycle?.status);
  useLiveClock(meta.label === '执行中');
  const retry = detail.actions?.retry;
  const duration = task.duration_ms ?? task.report.elapsed_ms
    ?? durationFrom(task.lifecycle?.started_at, task.lifecycle?.finished_at);

  const retryMutation = useMutation({
    mutationFn: () => api.retryTask(runId, taskId),
    onSuccess: (result) => {
      push('任务已重试', result.run_id ? result.run_id.slice(0, 8) : undefined);
      void queryClient.invalidateQueries({ queryKey: ['run', runId] });
      void queryClient.invalidateQueries({ queryKey: ['board', runId] });
      void queryClient.invalidateQueries({ queryKey: ['task', runId, taskId] });
      if (result.run_id && result.run_id !== runId) {
        navigate(`/runs/${result.run_id}/tasks/${encodeURIComponent(result.task_id || taskId)}`);
      }
    },
    onError: (error) => push('操作失败', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  return (
    <>
      <PageHeader
        eyebrow={`Task / ${task.carrier || 'task'}`}
        title={task.carrier || task.task_id}
        meta={
          <>
            {task.biztype || '—'}
            <span className="dot">·</span>
            {task.problem?.service_name || '—'}
            <span className="dot">·</span>
            {meta.label === '未开始' ? '等待执行' : `第 ${task.attempt || task.lifecycle?.attempt || 1} 次尝试`}
            <span className="dot">·</span>
            {formatDuration(duration)}
          </>
        }
        actions={
          <>
            <TaskStatusBadge result={task.result} lifecycle={task.lifecycle?.status} />
            {retry ? (
              <button
                className="btn primary"
                disabled={!retry.enabled || retryMutation.isPending}
                title={retry.reason}
                onClick={() => retryMutation.mutate()}
              >
                {retryMutation.isPending ? '正在提交…' : '重试任务'}
              </button>
            ) : null}
          </>
        }
      />

      <Tabs
        active={active}
        items={[
          { key: 'overview', label: '分析与结果', href: `/runs/${runId}/tasks/${encodeURIComponent(taskId)}` },
          { key: 'change', label: '代码差异', href: `/runs/${runId}/tasks/${encodeURIComponent(taskId)}/change` },
          { key: 'review', label: '审查结果', href: `/runs/${runId}/tasks/${encodeURIComponent(taskId)}/review` },
          { key: 'audit', label: '审计', count: task.steps.length, href: `/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit/steps` },
        ]}
      />
      {meta.label === '失败' && detail.run_error?.message ? (
        <p className="note" style={{ marginTop: 8 }}>{detail.run_error.message}</p>
      ) : null}
      <div style={{ marginTop: 12 }}>
        <Link className="link" to={`/runs/${runId}`}>← 返回批次</Link>
      </div>
    </>
  );
}

export function TaskAuditTabs({ active }: { active: string }) {
  const { runId = '', taskId = '' } = useParams();
  const base = `/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit`;
  return (
    <Tabs
      active={active}
      items={[
        { key: 'steps', label: '步骤', href: `${base}/steps` },
        { key: 'agent', label: 'Agent 对话', href: `${base}/agent` },
        { key: 'prompt', label: 'Prompt 快照', href: `${base}/prompt` },
        { key: 'artifacts', label: '任务产物', href: `${base}/artifacts` },
      ]}
    />
  );
}
