import { workbenchQueries } from '../../lib/workbenchQueries';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Result, Skeleton } from 'antd';
import { StatusBadge } from '../../components/StatusBadge';
import { RunProgress } from '../../components/RunProgress';
import { Banner, KeyValueList, Panel } from '../../components/ui';
import { formatTime, shortId } from '../../lib/format';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { InfoCircleOutlined } from '@ant-design/icons';
import { runDetailModel, readableRunFailure } from './runDetailModel';
import { RunControls } from './RunControls';
import { RunPhasePanel } from './RunPhasePanel';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { subscribeRunEvents, type StreamState } from '../../lib/runEvents';
import { TaskBoard } from './TaskBoard';
import { CheckpointTimeline } from './CheckpointTimeline';
import { ControlAuditPanel } from './ControlAuditPanel';
import styles from './RunWorkspace.module.css';

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: '实时连接中…',
  open: '实时连接已建立',
  closed: '实时连接已关闭',
  error: '实时连接中断，正在重连',
};

const PHASE_STATUS_TEXT: Record<string, string> = {
  failed: '执行失败', running: '正在执行', succeeded: '已完成', skipped: '未执行', pending: '等待执行', waiting: '等待处理',
};

export function RunWorkspace() {
  const { runId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [informationOpen, setInformationOpen] = useState(false);
  const [stream, setStream] = useState<StreamState>('connecting');
  const requestedTab = searchParams.get('tab');
  const aliases: Record<string, string> = { board: 'tasks', workflow: 'overview' };
  const changeTab = (value: string) => {
    const next = new URLSearchParams(searchParams); next.set('tab', value); setSearchParams(next, { replace: true });
  };

  const run = useQuery(workbenchQueries.run(runId));
  const board = useQuery({
    queryKey: queryKeys.board(runId),
    queryFn: () => api.board(runId, { page_size: 1 }),
    enabled: Boolean(runId),
    refetchInterval: run.data?.terminal ? false : 5000,
  });

  // 服务端负责跨记录的生命周期收敛，页面不重写运行状态。
  const runView = run.data;
  const requestedView = requestedTab || (runView?.task_count === 0 ? 'overview' : 'tasks');
  const tab = ['overview', 'tasks', 'checkpoints', 'audit'].includes(requestedView) ? requestedView : aliases[requestedView] || 'overview';

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

  const model = useMemo(() => runView ? runDetailModel(runView) : undefined, [runView]);
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
          extra={<Button type="primary" onClick={() => navigate('/runs')}>返回执行批次</Button>}
        />
      </div>
    );
  }
  if (!runView || !model) return null;
  const selectedKey = searchParams.get('phase') || model.focus;
  const selectedPhase = model.phases.find((phase) => phase.key === selectedKey) || model.phases[0];
  const selectPhase = (key: string) => {
    const next = new URLSearchParams(searchParams); next.set('phase', key); next.set('tab', 'overview'); setSearchParams(next, { replace: true });
  };
  const views = [{ key: 'tasks', label: `问题任务 · ${runView.task_count}` }, { key: 'overview', label: '执行案卷' }, { key: 'checkpoints', label: '恢复记录' }, { key: 'audit', label: '操作审计' }];
  const attentionTasks = runView.failed_tasks + runView.blocked_tasks + runView.manual_tasks;
  const phaseToneClass: Record<string, string> = {
    succeeded: styles.phaseSucceeded, failed: styles.phaseFailed, running: styles.phaseRunning,
    waiting: styles.phaseWaiting, skipped: styles.phasePending, pending: styles.phasePending,
  };

  return (
    <div className={`page ${styles.root}`}>
      <nav className={styles.crumbs} aria-label="面包屑">
        <button type="button" onClick={() => navigate('/runs')}>执行批次</button>
        <span>/</span>
        <code title={runId}>{shortId(runId, 12)}</code>
        <span className={styles.crumbsTail}>{runView.terminal ? '终态记录' : STREAM_LABEL[stream]}</span>
      </nav>

      <header className={styles.batchHeader}>
        <div className={styles.batchIdentity}>
          <div className={styles.batchTitle}><h1>自动修复批次</h1><code title={runId}>{shortId(runId, 24)}</code><StatusBadge kind="run" value={runView.status} /></div>
          <p>创建于 {formatTime(runView.started_at)}<span>·</span>{runView.read_only ? '历史工作流 · 只读' : runView.worker_active ? '批次持续推进中' : runView.terminal ? '本轮执行已结束' : '状态自动同步'}</p>
        </div>
        <div className={styles.headerActions}><Button icon={<InfoCircleOutlined />} onClick={() => setInformationOpen(true)}>运行信息</Button><RunControls run={runView} refresh={() => void run.refetch()} /></div>
      </header>

      <nav className={styles.tabs} aria-label="详情视图">
        {views.map((view) => (
          <button type="button" key={view.key} className={styles.tab} aria-current={tab === view.key ? 'page' : undefined} onClick={() => changeTab(view.key)}>{view.label}</button>
        ))}
      </nav>

      {tab === 'tasks' && runView.run_error?.message && <Banner tone="warn" extra={<Button size="small" onClick={() => changeTab('overview')}>查看执行案卷</Button>}>{readableRunFailure(runView.run_error.message, runView.run_error.terminal_step === 'sync_source_repo' || model.focus === 'source')}</Banner>}

      {!runView.terminal && runView.run_lifecycle?.progress?.step === 'sync_source_repo' && (
        <Banner tone="info" extra={`已用 ${runView.run_lifecycle.progress.counts.elapsed_seconds ?? 0} 秒 / 超时 ${runView.run_lifecycle.progress.counts.timeout_seconds ?? '未记录'} 秒`}>
          源码下载进度 · {runView.run_lifecycle.progress.message}
        </Banner>
      )}

      {tab === 'overview' && (
        <div className={styles.grid}>
          <aside className={styles.rail} aria-label="执行路径">
            {model.phases.map((phase, index) => (
              <button
                key={phase.key}
                type="button"
                title={PHASE_STATUS_TEXT[phase.status]}
                className={`${styles.phase} ${phaseToneClass[phase.status] ?? styles.phasePending}${phase.key === selectedPhase.key ? ` ${styles.phaseSelected}` : ''}`}
                aria-current={phase.key === selectedPhase.key ? 'step' : undefined}
                onClick={() => selectPhase(phase.key)}
              >
                <span className={styles.phaseDot} aria-hidden="true" />
                <span className={styles.phaseIndex}>{String(index + 1).padStart(2, '0')}</span>
                <span className={styles.phaseLabel}>{phase.title}<small>{PHASE_STATUS_TEXT[phase.status]}</small></span>
              </button>
            ))}
          </aside>

          <div className={styles.reading}>
            <RunPhasePanel key={selectedPhase.key} phase={selectedPhase} onTasks={() => changeTab('tasks')}
              nextAction={runView.read_only ? '确认连接环境和仓库访问权限恢复后，可返回执行批次新建一轮执行。' : model.nextAction}
              blockedBeforeTasks={runView.task_count === 0} />
            {runView.task_count === 0 && selectedPhase.status !== 'failed' && (
              <div className={styles.taskNote}>
                <div>
                  <strong>尚未派发问题任务</strong>
                  <p>任务将在源码准备、日志采集和问题识别完成后生成。</p>
                </div>
                <span>0 项</span>
              </div>
            )}
          </div>


        </div>
      )}

      {tab === 'tasks' && <>
        {runView.waiting_tasks > 0 && <section className={styles.approvalNotice} aria-label="批次待审批提示">
          <div><strong>{runView.waiting_tasks} 个任务等待审批{runView.worker_active ? '，批次继续执行' : ''}</strong><p>修复分支已交付，等待工程师核对后决定保留或打回。</p></div>
          <Button type="primary" onClick={() => navigate(`/approvals?run=${encodeURIComponent(runId)}`)}>前往审批（{runView.waiting_tasks}）</Button>
        </section>}
        <section className={styles.batchProgress} aria-label="批次处理进度"><RunProgress run={runView} detailed /></section>
        <div className={styles.taskGrid}>
          <TaskBoard runId={runId} stages={board.data?.stages} onOpen={(task) => navigate(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(task.task_id)}`)} />
          <aside className={styles.facts} aria-label="批次配置">
            <Panel title="批次配置参数"><KeyValueList items={[
              { label: '执行方式', value: '任务串行推进', text: true },
              { label: '日志窗口', value: runView.config.log_hours === undefined ? '未记录' : `${runView.config.log_hours} 小时`, text: true },
              { label: '日志上限', value: String(runView.config.log_limit ?? '未记录') },
              { label: '最大尝试次数', value: String(runView.config.max_attempts ?? '未记录') },
              { label: '审查方式', value: runView.config.review_mode === 'human' ? '人工审批' : runView.config.review_mode === 'agent' ? '自动审查' : '未记录', text: true },
              { label: '操作人', value: runView.operator || '未记录', text: true },
            ]} /></Panel>
            {model.nextAction && <Panel title="当前进展"><p className={styles.progressNote}>{model.nextAction}</p></Panel>}
            <Panel title="批次流水" extra={runView.terminal ? '已归档' : '实时同步'}>
              <ol className={styles.runTimeline}>{[...runView.steps].filter((step) => step.status !== 'pending').slice(-5).reverse().map((step) => <li key={step.name}><strong>{step.title || step.name}</strong><time>{formatTime(step.finished_at || step.started_at)}</time><p>{step.assessment?.summary || step.summary || PHASE_STATUS_TEXT[step.status]}</p></li>)}</ol>
              {!runView.steps.length && <p className={styles.progressNote}>{runView.run_lifecycle?.progress?.message || '批次执行记录尚未生成'}</p>}
            </Panel>
            <Panel title="执行时间"><KeyValueList items={[
              { label: '开始', value: formatTime(runView.started_at) },
              { label: '结束', value: formatTime(runView.finished_at) },
            ]} /></Panel>
          </aside>
        </div>
      </>}
      {tab === 'checkpoints' && <CheckpointTimeline runId={runId} />}
      {tab === 'audit' && <ControlAuditPanel runId={runId} />}
      <WorkbenchDrawer title="批次运行信息" open={informationOpen} onClose={() => setInformationOpen(false)} width="min(480px, 100vw)">          <aside className={styles.facts} aria-label="本轮信息">
            <Panel title="本轮信息">
              <KeyValueList items={[
                { label: '任务总数', value: runView.task_count },
                { label: '已完成', value: runView.succeeded_tasks },
                { label: '待审批', value: runView.waiting_tasks },
                { label: '需关注', value: attentionTasks, tone: attentionTasks > 0 ? 'bad' : undefined },
                { label: '结束时间', value: formatTime(runView.finished_at) },
                { label: '日志窗口', value: runView.config.log_hours === undefined ? '未记录' : `${runView.config.log_hours} 小时`, text: true },
              ]} />
            </Panel>
            {model.recoveryNode && (
              <Banner tone="primary" extra={`${runView.terminal ? '结束' : '开始'} ${formatTime(runView.finished_at ?? runView.started_at)}`}>
                恢复位置 <code>{model.recoveryNode}</code> · {runView.terminal ? '这是重试时的起点，当前没有执行此节点。' : '用于中断后恢复，与节点实时状态独立。'}
              </Banner>
            )}
          </aside></WorkbenchDrawer>
    </div>
  );
}
