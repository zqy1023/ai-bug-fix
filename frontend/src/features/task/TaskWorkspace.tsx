import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Dropdown, Result, Skeleton, Tabs } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../lib/api';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { TaskHeader } from './TaskHeader';
import { TaskVerdictBar } from './TaskVerdictBar';
import { RootCauseSection } from './RootCauseSection';
import { ChangeSection } from './ChangeSection';
import { EvidenceSection } from './EvidenceSection';
import { DeliverySection } from './DeliverySection';
import { TimelineSection } from './TimelineSection';
import { WorkflowMap } from './WorkflowMap';
import { TaskSiblings } from './TaskSiblings';
import { TaskMetadata } from './TaskMetadata';
import { TaskOperations } from './TaskOperations';
import { ACTIVE_RUN, taskIsActive } from '../../lib/lifecycle';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { buildTaskView, labelOf } from './taskVerdict';
import { useTaskEvidence } from './useTaskEvidence';
import { defaultStep } from './stages';
import { workflowSteps } from './reviewEvidence';
import styles from './TaskWorkspace.module.css';

export function TaskWorkspace() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const [queueOpen, setQueueOpen] = useState(false);
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const task = useQuery(workbenchQueries.task(runId, taskId));
  const evidence = useTaskEvidence(task.data);
  const view = useMemo(() => (task.data ? buildTaskView(task.data, evidence) : undefined), [task.data, evidence]);

  const steps = workflowSteps(task.data?.task.steps ?? []);
  const executionActive = Boolean(
    task.data && (task.data.worker_active || taskIsActive(task.data.task) || ACTIVE_RUN.has(task.data.run_status)),
  );
  const activeStep = task.data?.task.current_step || (steps.find((s) => s.status === 'running')?.name ?? '');
  const activeStepLabel = labelOf(activeStep);

  const requestedView = searchParams.get('view');
  const normalizeTab = (raw: string | null) => {
    if (!raw) return 'root';
    if (raw === 'overview') return 'root';
    if (raw === 'diff') return 'changes';
    if (raw === 'execution' || raw === 'dossier') return 'timeline';
    return ['root', 'changes', 'evidence', 'delivery', 'timeline', 'flow'].includes(raw) ? raw : 'root';
  };
  const tab = searchParams.get('step') ? 'timeline' : normalizeTab(requestedView);

  const selectTab = (nextTab: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', nextTab);
    next.delete('step');
    next.delete('trace');
    setSearchParams(next, { replace: true });
  };

  const handleOpenStep = (stepName: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', 'timeline');
    next.set('step', stepName);
    setSearchParams(next, { replace: true });
  };

  const handleVerdictTileOpen = (tileKey: 'root' | 'changes' | 'evidence' | 'delivery' | 'timeline') => {
    selectTab(tileKey);
  };

  if (task.isLoading) {
    return (
      <div className="page">
        <Skeleton active paragraph={{ rows: 8 }} />
      </div>
    );
  }

  if (task.isError) {
    const notFound = task.error instanceof ApiError && task.error.status === 404;
    return (
      <div className="page">
        <Result
          status={notFound ? '404' : 'error'}
          title={notFound ? '任务不存在' : '任务详情加载失败'}
          subTitle={task.error instanceof ApiError ? task.error.message : '请检查工作流服务'}
          extra={
            <Button type="primary" onClick={() => navigate(`/runs/${encodeURIComponent(runId)}`)}>
              返回运行批次
            </Button>
          }
        />
      </div>
    );
  }

  if (!task.data || !view) return null;

  return (
    <div className={`page ${styles.root}`}>
      <TaskHeader detail={task.data} actions={<div className={styles.utility}>
          <Button size="small" onClick={() => setQueueOpen(true)}>
            同批次任务
          </Button>
          <Button size="small" onClick={() => setMetadataOpen(true)}>
            任务信息
          </Button>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [{ key: 'flow', label: '编排状态机' }],
              onClick: ({ key }) => selectTab(key),
            }}
          >
            <Button size="small">更多记录</Button>
          </Dropdown>
      </div>} />
      {task.data.read_only && (
        <Alert
          type="info"
          showIcon
          message="历史工作流只读"
          description="可以查看全部记录和证据；如需重新执行，请创建新批次。"
        />
      )}
      <div className={styles.workspace}>
        <WorkbenchDrawer
          title="同批次任务"
          open={queueOpen}
          onClose={() => setQueueOpen(false)}
          width="min(400px, 100vw)"
          styles={{ body: { padding: 0 } }}
        >
          <TaskSiblings runId={runId} taskId={taskId} />
        </WorkbenchDrawer>
        <div className={styles.center}>
          <TaskVerdictBar
            view={view}
            activeStepLabel={activeStepLabel}
            onOpenTab={handleVerdictTileOpen}
          />
          <Tabs
            className={styles.tabs}
            activeKey={tab}
            onChange={selectTab}
            items={[
              { key: 'root', label: '根因分析' },
              { key: 'changes', label: `代码改动 · ${view.changes.count}` },
              { key: 'evidence', label: '验证证据' },
              { key: 'delivery', label: 'Git 交付' },
              { key: 'timeline', label: `执行时间线 · ${steps.length}` },
              ...(tab === 'flow' ? [{ key: 'flow', label: '编排状态机' }] : []),
            ]}
          />
          {tab === 'root' && (
            <RootCauseSection detail={task.data} view={view} onOpenStep={handleOpenStep} />
          )}
          {tab === 'changes' && (
            <ChangeSection detail={task.data} view={view} onOpenStep={handleOpenStep} />
          )}
          {tab === 'evidence' && (
            <EvidenceSection detail={task.data} view={view} onOpenStep={handleOpenStep} />
          )}
          {tab === 'delivery' && (
            <DeliverySection detail={task.data} view={view} onOpenStep={handleOpenStep} />
          )}
          {tab === 'timeline' && (
            <TimelineSection
              detail={task.data}
              view={view}
              runActive={executionActive}
              approvalPending={Boolean(task.data.task.approval_pending)}
              onOpenStep={handleOpenStep}
            />
          )}
          {tab === 'flow' && (
            <WorkflowMap
              steps={steps}
              stages={task.data.stages}
              selected={searchParams.get('step') || defaultStep(steps)}
              onInspect={(name, evTrace) => {
                const next = new URLSearchParams(searchParams);
                next.set('view', 'timeline');
                next.set('step', name);
                if (evTrace) next.set('trace', evTrace);
                else next.delete('trace');
                setSearchParams(next, { replace: true });
              }}
            />
          )}
          <TaskOperations
            detail={task.data}
            onInspect={() => handleOpenStep(defaultStep(steps))}
            onRefresh={() => void task.refetch()}
            refreshing={task.isFetching}
          />
        </div>
        <WorkbenchDrawer
          title="任务信息与最近记录"
          open={metadataOpen}
          onClose={() => setMetadataOpen(false)}
          width="min(440px, 100vw)"
        >
          <TaskMetadata detail={task.data} />
        </WorkbenchDrawer>
      </div>
    </div>
  );
}
