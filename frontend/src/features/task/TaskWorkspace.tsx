import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Drawer, FloatButton, Result, Skeleton } from 'antd';
import { FileSearchOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { TaskHeader } from './TaskHeader';
import { NodeRail } from './NodeRail';
import { StepDetail } from './StepDetail';
import { ContextInspector } from './ContextInspector';
import { TaskConclusion, analysisOf } from './TaskConclusion';
import { defaultStep, stepRecord } from './stages';
import { EmptyState } from '../../components/EmptyState';

const ACTIVE = new Set(['queued', 'running', 'pause_requested', 'cancel_requested']);

export function TaskWorkspace() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const task = useQuery({
    queryKey: queryKeys.task(runId, taskId),
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
    refetchInterval: 5000,
  });

  const steps = task.data?.task.steps ?? [];
  const [conclusionOpen, setConclusionOpen] = useState(false);
  const selected = searchParams.get('step') || (steps.length ? defaultStep(steps) : '');
  const step = useMemo(() => (selected ? stepRecord(steps, selected) : undefined), [steps, selected]);

  const detail = useQuery({
    queryKey: queryKeys.step(runId, taskId, selected),
    queryFn: () => api.step(runId, taskId, selected),
    enabled: Boolean(runId && taskId && selected && step),
    refetchInterval: 5000,
  });

  const selectStep = (name: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('step', name);
    next.delete('trace');
    setSearchParams(next, { replace: true });
  };

  if (task.isLoading) {
    return <div className="page"><Skeleton active paragraph={{ rows: 10 }} /></div>;
  }
  if (task.isError) {
    const notFound = task.error instanceof ApiError && task.error.status === 404;
    return (
      <div className="page">
        <Result
          status={notFound ? '404' : 'error'}
          title={notFound ? '任务不存在' : '任务详情加载失败'}
          subTitle={task.error instanceof ApiError ? task.error.message : '请检查工作流服务'}
          extra={<Button type="primary" onClick={() => navigate(`/runs/${encodeURIComponent(runId)}`)}>返回执行工作区</Button>}
        />
      </div>
    );
  }
  if (!task.data) return null;

  const runActive = ACTIVE.has(task.data.run_status);
  const hasAnalysis = Boolean(analysisOf(steps));

  return (
    <div className="page task-workspace">
      <TaskHeader detail={task.data} />
      {!steps.length ? (
        <EmptyState
          title="该任务还没有步骤记录"
          description="任务可能仍在排队，或执行记录尚未落盘。"
          action={<Button onClick={() => void task.refetch()}>刷新</Button>}
        />
      ) : (
        <div className="task-layout">
          <NodeRail steps={steps} selected={selected} onSelect={selectStep} task={task.data.task} />
          <div className="task-main">
            {step ? (
              <StepDetail
                runId={runId}
                taskId={taskId}
                step={step}
                detail={detail.data}
                runActive={runActive}
              />
            ) : (
              <EmptyState
                title={selected ? '该步骤尚未执行' : '选择一个步骤'}
                description={selected
                  ? '执行推进到该节点后会生成输入、输出与审计记录。'
                  : '从左侧任务节点轨选择要观察的步骤。'}
              />
            )}
            <ContextInspector context={detail.data?.context} />
          </div>
        </div>
      )}

      {hasAnalysis && (
        <>
          <FloatButton
            icon={<FileSearchOutlined />}
            description="分析结论"
            shape="square"
            type="primary"
            className="conclusion-fab"
            onClick={() => setConclusionOpen(true)}
          />
          <Drawer
            title="任务分析结论"
            placement="right"
            width={560}
            open={conclusionOpen}
            onClose={() => setConclusionOpen(false)}
            styles={{ body: { paddingTop: 8 } }}
          >
            <TaskConclusion task={task.data.task} />
          </Drawer>
        </>
      )}
    </div>
  );
}
