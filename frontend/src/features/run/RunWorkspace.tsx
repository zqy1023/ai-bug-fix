import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Result, Skeleton, Tabs } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { subscribeRunEvents, type StreamState } from '../../lib/runEvents';
import { RunHeader } from './RunHeader';
import { RunGraph } from './RunGraph';
import { TaskBoard } from './TaskBoard';
import { CheckpointTimeline } from './CheckpointTimeline';
import { ControlAuditPanel } from './ControlAuditPanel';

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: '实时连接中…',
  open: '实时连接已建立',
  closed: '实时连接已关闭',
  error: '实时连接中断，正在重连',
};

export function RunWorkspace() {
  const { runId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [stream, setStream] = useState<StreamState>('connecting');
  const tab = searchParams.get('tab') ?? 'board';

  const run = useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.run(runId),
    enabled: Boolean(runId),
    // SSE 是主通道；轮询是兜底，终态确认后才停止。
    refetchInterval: (query) => (query.state.data?.terminal ? false : 5000),
  });
  const board = useQuery({
    queryKey: queryKeys.board(runId),
    queryFn: () => api.board(runId),
    enabled: Boolean(runId),
    refetchInterval: 5000,
  });

  // 任务板比 run 详情更细粒度；只要还有活跃任务，就不能渲染成已完成。
  const boardActive = (board.data?.tasks ?? []).some((task) => {
    const value = String(task.result || '').toUpperCase();
    return value === 'RUNNING' || value === 'PENDING' || value === 'WAITING_APPROVAL';
  });
  const runView = useMemo(() => {
    if (!run.data) return undefined;
    const staleTerminal = !run.data.terminal
      && boardActive
      && ['succeeded', 'failed', 'partial_failed', 'cancelled'].includes(run.data.status);
    return staleTerminal ? { ...run.data, status: 'running' as const, terminal: false } : run.data;
  }, [run.data, boardActive]);

  useEffect(() => {
    if (!runId) return;
    return subscribeRunEvents(runId, {
      onSnapshot: () => {
        void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.board(runId) });
      },
      onState: (state) => {
        setStream(state);
        if (state === 'closed') {
          void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.board(runId) });
        }
      },
    });
  }, [runId, queryClient]);

  const items = useMemo(() => [
    {
      key: 'board',
      label: '任务板',
      children: (
        <TaskBoard
          tasks={board.data?.tasks ?? []}
          onOpen={(task) => navigate(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(task.task_id)}`)}
        />
      ),
    },
    { key: 'checkpoints', label: 'Checkpoint 时间线', children: <CheckpointTimeline runId={runId} /> },
    { key: 'audit', label: '控制审计', children: <ControlAuditPanel runId={runId} /> },
  ], [board.data?.tasks, navigate, runId]);

  if (run.isLoading) {
    return <div className="page"><Skeleton active paragraph={{ rows: 8 }} /></div>;
  }
  if (run.isError) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    return (
      <div className="page">
        <Result
          status={notFound ? '404' : 'error'}
          title={notFound ? '执行不存在' : '执行详情加载失败'}
          subTitle={run.error instanceof ApiError ? run.error.message : '请检查工作流服务'}
          extra={<Button type="primary" onClick={() => navigate('/runs')}>返回执行阵列</Button>}
        />
      </div>
    );
  }
  if (!runView) return null;

  const streamingClosed = stream === 'closed' && !runView.terminal;

  return (
    <div className="page run-workspace">
      <RunHeader run={runView} />
      <RunGraph run={runView} />
      <div className={'live-line is-' + stream + (streamingClosed ? ' is-polling' : '')}>
        <span className="live-dot" />
        {streamingClosed ? '实时连接已关闭，正在轮询刷新' : STREAM_LABEL[stream]}
        {board.isFetching && <span className="live-refreshing">同步中</span>}
      </div>
      <Tabs
        activeKey={tab}
        onChange={(key) => setSearchParams({ tab: key }, { replace: true })}
        items={items}
        className="workspace-tabs"
      />
    </div>
  );
}
