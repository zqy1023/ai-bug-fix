import { useMemo } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Result, Skeleton, Tabs } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { TaskHeader } from './TaskHeader';
import { ExecutionNav } from './ExecutionNav';
import { StepDetail } from './StepDetail';
import { ContextInspector } from './ContextInspector';
import { TaskOverview } from './TaskOverview';
import { defaultStep, stepRecord } from './stages';
import { TaskOperations } from './TaskOperations';
import { ACTIVE_RUN, taskIsActive, taskHasFailed } from '../../lib/lifecycle';
import { EmptyState } from '../../components/EmptyState';


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
  const executionActive = Boolean(task.data && (task.data.worker_active || taskIsActive(task.data.task) || ACTIVE_RUN.has(task.data.run_status)));
  const tab = searchParams.get('view') || (searchParams.get('step') || (task.data && taskHasFailed(task.data.task)) ? 'execution' : 'overview');
  const executionOpen = tab === 'execution';
  const selected = searchParams.get('step') || (steps.length ? defaultStep(steps) : '');
  const step = useMemo(() => (selected ? stepRecord(steps, selected) : undefined), [steps, selected]);

  const detail = useQuery({
    queryKey: queryKeys.step(runId, taskId, selected),
    queryFn: () => api.step(runId, taskId, selected),
    enabled: Boolean(executionOpen && runId && taskId && selected && step),
    refetchInterval: executionOpen && executionActive ? 5000 : false,
  });

  const selectStep = (name: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('step', name);
    next.delete('trace');
    next.set('view', 'execution');
    setSearchParams(next, { replace: true });
  };

  if (task.isLoading) {
    return <div className="page"><Skeleton active paragraph={{ rows: 8 }} /></div>;
  }
  if (task.isError) {
    const notFound = task.error instanceof ApiError && task.error.status === 404;
    return (
      <div className="page">
        <Result
          status={notFound ? '404' : 'error'}
          title={notFound ? '任务不存在' : '任务详情加载失败'}
          subTitle={task.error instanceof ApiError ? task.error.message : '请检查工作流服务'}
          extra={<Button type="primary" onClick={() => navigate(`/runs/${encodeURIComponent(runId)}`)}>返回运行批次</Button>}
        />
      </div>
    );
  }
  if (!task.data) return null;

  const runActive = executionActive;
  const approvalRecoveryFailed = task.data.task.approval_pending === true
    && task.data.run_status === 'failed'
    && String(task.data.run_error?.status || '').toUpperCase() === 'FAILED';
  const executedCount = steps.filter((record) => record.status !== 'pending').length;
  const terminalStep = approvalRecoveryFailed
    ? '审批恢复'
    : task.data.task.report?.terminal_step || '';

  return (
    <div className="page task-workspace">
      <TaskHeader detail={task.data} />
      <TaskOperations detail={task.data} onInspect={() => selectStep(defaultStep(steps))} onRefresh={() => void task.refetch()} refreshing={task.isFetching} />
      <Tabs
        className="task-view-tabs"
        activeKey={tab}
        onChange={(view) => { const next = new URLSearchParams(searchParams); next.set('view', view); setSearchParams(next, { replace: true }); }}
        items={[
          { key: 'overview', label: '结果与交付' },
          { key: 'execution', label: `执行记录 · ${steps.length}` },
        ]}
      />
      {tab === 'overview' && <TaskOverview detail={task.data} onOpenStep={selectStep} />}
      {executionOpen && <section className="execution-section">
        <header className="execution-toolbar"><div><strong>执行记录</strong><span>选择节点查看结果、证据与调用记录</span></div><span>{steps.filter((item) => item.status === 'failed').length} 个执行异常 · {steps.length} 个节点</span></header>
        {approvalRecoveryFailed && <Alert type="error" showIcon message="审批恢复失败" description={task.data.run_error?.message || '请在交付审批页重新提交决策'} />}

        {executionOpen && (
          steps.length ? (
            <div className="task-layout execution-layout">
              <ExecutionNav
                steps={steps}
                selected={selected}
                onSelect={selectStep}
                stages={task.data.stages}
              />
              <div className="task-main">
                {detail.isError && <Alert type="error" showIcon message="步骤详情暂时无法加载" description={detail.error instanceof Error ? detail.error.message : '请刷新重试'} action={<Button size="small" onClick={() => void detail.refetch()}>重试加载</Button>} />}
                {step ? (
                  <StepDetail
                    key={selected}
                    runId={runId}
                    taskId={taskId}
                    step={step}
                    detail={detail.data}
                    runActive={runActive}
                    approvalPending={Boolean(task.data.task.approval_pending)}
                  />
                ) : (
                  <EmptyState
                    title={selected ? '该步骤尚未执行' : '当前没有步骤记录'}
                    description="执行推进后会生成步骤输入、输出和审计信息。"
                  />
                )}
                <ContextInspector context={detail.data?.context} />
              </div>
            </div>
          ) : (
            <EmptyState
              title="暂无原子步骤记录"
              description="任务可能仍在排队，或者后端尚未落盘步骤执行记录。"
              action={<Button onClick={() => void task.refetch()}>刷新任务</Button>}
            />
          )
        )}

        <footer className="execution-section-footer">
          <span>已记录 {steps.length} 个步骤{steps.length ? ` · ${executedCount} 个已开始或已结束` : ''}</span>
          {terminalStep && <span>业务停止点：{terminalStep}</span>}
        </footer>
      </section>}
    </div>
  );
}
