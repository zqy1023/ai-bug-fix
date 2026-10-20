import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Result, Skeleton } from 'antd';
import { CodeOutlined, DownOutlined, RightOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { TaskHeader } from './TaskHeader';
import { NodeRail } from './NodeRail';
import { StepDetail } from './StepDetail';
import { ContextInspector } from './ContextInspector';
import { TaskOverview } from './TaskOverview';
import { defaultStep, stepRecord } from './stages';
import { EmptyState } from '../../components/EmptyState';

const ACTIVE = new Set(['queued', 'running', 'pause_requested', 'cancel_requested', 'waiting_approval']);

export function TaskWorkspace() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [executionOpen, setExecutionOpen] = useState(Boolean(searchParams.get('step')));

  const task = useQuery({
    queryKey: queryKeys.task(runId, taskId),
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
    refetchInterval: 5000,
  });

  const steps = task.data?.task.steps ?? [];
  const selected = searchParams.get('step') || (steps.length ? defaultStep(steps) : '');
  const step = useMemo(() => (selected ? stepRecord(steps, selected) : undefined), [steps, selected]);

  const detail = useQuery({
    queryKey: queryKeys.step(runId, taskId, selected),
    queryFn: () => api.step(runId, taskId, selected),
    enabled: Boolean(executionOpen && runId && taskId && selected && step),
    refetchInterval: executionOpen && ACTIVE.has(task.data?.run_status ?? '') ? 5000 : false,
  });

  const selectStep = (name: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('step', name);
    next.delete('trace');
    setSearchParams(next, { replace: true });
    setExecutionOpen(true);
  };

  const openExecution = () => setExecutionOpen(true);

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

  const runActive = ACTIVE.has(task.data.run_status);
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
      <TaskOverview detail={task.data} onOpenStep={selectStep} />

      <section className="execution-section">
        <header className="execution-section-heading">
          <div className="execution-heading-icon"><CodeOutlined /></div>
          <div className="execution-heading-copy">
            <h3>执行记录与调试</h3>
            <p>{approvalRecoveryFailed
              ? '后台恢复失败；待审批记录仍保留。请先处理恢复异常，再回到交付审批重试决策。'
              : '查看节点输入、输出、Agent 会话、错误日志和产物；默认收起。'}</p>
          </div>
          <Button
            type={executionOpen ? 'default' : 'primary'}
            onClick={() => setExecutionOpen((open) => !open)}
            icon={executionOpen ? <DownOutlined /> : <RightOutlined />}
          >
            {executionOpen ? '收起详情' : '查看节点详情'}
          </Button>
        </header>

        {executionOpen && (
          steps.length ? (
            <div className="task-layout">
              <NodeRail
                steps={steps}
                selected={selected}
                onSelect={selectStep}
                task={task.data.task}
                recoveryError={approvalRecoveryFailed ? task.data.run_error : undefined}
              />
              <div className="task-main">
                {step ? (
                  <StepDetail
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
      </section>
    </div>
  );
}
