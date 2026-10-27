import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Result, Skeleton, Tabs, Dropdown } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../lib/api';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { WorkflowMap } from './WorkflowMap';
import { TaskHeader } from './TaskHeader';
import { ExecutionNav } from './ExecutionNav';
import { StepDetail } from './StepDetail';
import { ContextInspector } from './ContextInspector';
import { TaskOverview } from './TaskOverview';
import { TaskReviewSummary } from './TaskReviewSummary';
import { TaskSiblings } from './TaskSiblings';
import { TaskMetadata } from './TaskMetadata';
import { TaskDiff } from './TaskDiff';
import { defaultStep, stepRecord } from './stages';
import { TaskOperations } from './TaskOperations';
import { ACTIVE_RUN, taskIsActive } from '../../lib/lifecycle';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { EmptyState } from '../../components/EmptyState';
import styles from './TaskWorkspace.module.css';


export function TaskWorkspace() {
  const { runId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const [queueOpen, setQueueOpen] = useState(false);
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const task = useQuery(workbenchQueries.task(runId, taskId));

  const steps = task.data?.task.steps ?? [];
  const executionActive = Boolean(task.data && (task.data.worker_active || taskIsActive(task.data.task) || ACTIVE_RUN.has(task.data.run_status)));
  const requestedView = searchParams.get('view');
  const tab = (requestedView && ['overview', 'diff', 'execution', 'dossier', 'flow'].includes(requestedView) ? requestedView : '') || (searchParams.get('step') ? 'execution' : 'overview');
  const executionOpen = tab === 'execution';
  const selected = searchParams.get('step') || (steps.length ? defaultStep(steps) : '');
  const step = useMemo(() => (selected ? stepRecord(steps, selected) : undefined), [steps, selected]);

  const detail = useQuery({
    ...workbenchQueries.step(runId, taskId, selected),
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
    <div className={`page ${styles.root}`}>
      <TaskHeader detail={task.data} />
      <div className={styles.commandBar}>
        <TaskOperations detail={task.data} onInspect={() => selectStep(defaultStep(steps))} onRefresh={() => void task.refetch()} refreshing={task.isFetching} />
        <div className={styles.utility}><Button onClick={() => setQueueOpen(true)}>同批次任务</Button><Button onClick={() => setMetadataOpen(true)}>任务信息</Button><Dropdown trigger={['click']} menu={{items:[{key:'flow',label:'编排状态机'},{key:'dossier',label:'完整案卷'}],onClick:({key})=>{const next=new URLSearchParams(searchParams);next.set('view',key);setSearchParams(next,{replace:true});}}}><Button>更多记录</Button></Dropdown></div>
      </div>
      {task.data.read_only && <Alert type="info" showIcon message="历史工作流只读" description="可以查看全部记录和证据；如需重新执行，请创建新批次。" />}
      <div className={`${styles.workspace} ${executionOpen ? styles.executionLayout : ''}`}>
      <WorkbenchDrawer title="同批次任务" open={queueOpen} onClose={() => setQueueOpen(false)} width="min(400px, 100vw)" styles={{body:{padding:0}}}><TaskSiblings runId={runId} taskId={taskId} /></WorkbenchDrawer>
      <div className={styles.center}>
      <Tabs
        className={styles.tabs}
        activeKey={tab}
        onChange={(view) => { const next = new URLSearchParams(searchParams); next.set('view', view); setSearchParams(next, { replace: true }); }}
        items={[
          { key: 'overview', label: '修复概览' },
          { key: 'diff', label: '代码改动' },
          { key: 'execution', label: `执行记录 · ${steps.length}` },
          ...(['flow','dossier'].includes(tab) ? [{key:tab,label:tab === 'flow' ? '编排状态机' : '完整案卷'}] : []),

        ]}
      />
      {tab === 'overview' && <TaskReviewSummary detail={task.data} onOpenStep={selectStep} />}
      {tab === 'flow' && <WorkflowMap steps={steps} stages={task.data.stages} selected={selected} onInspect={(name,evidence) => { const next = new URLSearchParams(searchParams); next.set('view','execution'); next.set('step',name); if(evidence) next.set('trace',evidence); else next.delete('trace'); setSearchParams(next,{replace:true}); }} />}
      {tab === 'diff' && <TaskDiff detail={task.data} />}
      {tab === 'dossier' && <TaskOverview detail={task.data} onOpenStep={selectStep} />}
      {executionOpen && <section className={styles.execSection}>
        <header className={styles.execHead}>
          <div><strong>执行记录</strong><span>选择阶段查看结果、证据与调用记录</span></div>
          <span className={styles.execHeadMeta}>{steps.filter((item) => item.status === 'failed').length} 个执行异常 · {steps.length} 个节点</span>
        </header>
        {approvalRecoveryFailed && <Alert type="error" showIcon message="审批恢复失败" description={task.data.run_error?.message || '请在交付审批页重新提交决策'} />}

        <div className={styles.executionGrid}>
        <aside className={styles.stageRail}><h3>执行阶段</h3><ExecutionNav vertical steps={steps} selected={selected} stages={task.data.stages} onSelect={selectStep} /></aside>
        {steps.length ? (
          <div className={styles.execBody}>
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
        ) : (
          <EmptyState
            title="暂无原子步骤记录"
            description="任务可能仍在排队，或者后端尚未落盘步骤执行记录。"
            action={<Button onClick={() => void task.refetch()}>刷新任务</Button>}
          />
        )}

        </div>
        <footer className={styles.foot}>
          <span>已记录 {steps.length} 个步骤{steps.length ? ` · ${executedCount} 个已开始或已结束` : ''}</span>
          {terminalStep && <span>业务停止点：{terminalStep}</span>}
        </footer>
      </section>}
      </div>
      {!executionOpen && <div className={styles.sideContext}><TaskMetadata detail={task.data} /></div>}
      <WorkbenchDrawer title="任务信息与最近记录" open={metadataOpen} onClose={() => setMetadataOpen(false)} width="min(440px, 100vw)"><TaskMetadata detail={task.data} /></WorkbenchDrawer>
      </div>
    </div>
  );
}
