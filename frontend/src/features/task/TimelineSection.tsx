import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Tabs } from 'antd';
import { AlertOutlined, ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration, formatTime } from '../../lib/format';
import { toneForStep, type StepTone } from '../../lib/stepPresentation';
import { StatusBadge } from '../../components/StatusBadge';
import { JsonDocument } from '../../components/JsonDocument';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { EmptyState } from '../../components/EmptyState';
import { AgentTrace } from '../agent/AgentTrace';
import { ContextInspector } from './ContextInspector';
import { StepActionBar } from './StepActionBar';
import { StepAssessment } from './StepAssessment';
import { StepOutcome } from './StepOutcome';
import { workflowSteps } from './reviewEvidence';
import { labelOf, type TaskView } from './taskVerdict';
import { TASK_STAGES } from './stages';
import { Section as TaskSection, ToneTag } from '../../components/section/Section';
import styles from './TaskSections.module.css';

const MODEL_STEPS = new Set(['locate_agent', 'repair_agent', 'review_agent', 'unified_fixer_agent']);
const TONE_CLASS: Record<StepTone, string> = {
  success: 'dotOk', danger: 'dotBad', running: 'dotRun', waiting: 'dotWait', manual: 'dotWait',
  neutral: 'dotOk', pending: 'dotPend', skipped: 'dotPend', cancelled: 'dotMute',
};
const TONE_TAG: Record<StepTone, { tone: 'ok' | 'bad' | 'warn' | 'info' | 'muted'; text: string }> = {
  success: { tone: 'ok', text: '已完成' },
  danger: { tone: 'bad', text: '未通过' },
  running: { tone: 'info', text: '执行中' },
  waiting: { tone: 'warn', text: '等待中' },
  manual: { tone: 'warn', text: '需人工处理' },
  neutral: { tone: 'muted', text: '已记录' },
  pending: { tone: 'muted', text: '未执行' },
  skipped: { tone: 'muted', text: '已跳过' },
  cancelled: { tone: 'muted', text: '已取消' },
};

/**
 * 执行时间线：展示主工作流阶段，历史审查工具保留在归档中，
 * 失败节点在顶部做成快跳入口，点击任意节点打开步骤抽屉。
 */
export function TimelineSection({ detail, view, runActive, approvalPending, onOpenStep }: {
  detail: TaskDetailResponse;
  view: TaskView;
  runActive: boolean;
  approvalPending: boolean;
  onOpenStep: (name: string) => void;
}) {
  const steps = detail.task.steps ?? [];
  const [selected, setSelected] = useState('');

  const rows = useMemo(() => {
    const main = workflowSteps(steps).filter(step => step.status !== 'pending' || step.name === view.activeStep);
    // 按工作流阶段顺序排；没有时间戳或同一阶段的记录保持原始相对次序，
    // 避免“等待中”的节点因为缺时间被排到最前。
    const stageIndex = (name: string): number => {
      const stages = detail.stages?.length ? detail.stages : TASK_STAGES;
      const index = stages.findIndex((stage) => stage.members.includes(name));
      return index < 0 ? stages.length : index;
    };
    const original = new Map(main.map((step, index) => [step, index] as const));
    const ordered = [...main].sort((left, right) => {
      const byStage = stageIndex(left.name) - stageIndex(right.name);
      if (byStage !== 0) return byStage;
      const leftTime = Date.parse(left.started_at || left.finished_at || '') || 0;
      const rightTime = Date.parse(right.started_at || right.finished_at || '') || 0;
      if (leftTime !== rightTime) return leftTime - rightTime;
      return (original.get(left) ?? 0) - (original.get(right) ?? 0);
    });
    return { ordered };
  }, [steps, view.activeStep, detail.stages]);

  const failed = rows.ordered.find((step) => toneForStep(step) === 'danger');
  const selectedStep = rows.ordered.find((step) => step.name === selected);

  const open = useCallback((name: string) => {
    setSelected(name);
  }, []);

  return (
    <TaskSection
      title="执行时间线"
      subtitle="点击任意节点查看输入、输出与产物"
      extra={<ToneTag tone={failed ? 'bad' : 'muted'}>{failed ? `${rows.ordered.filter((step) => toneForStep(step) === 'danger').length} 个执行异常` : `${rows.ordered.length} 条记录`}</ToneTag>}
    >
      {failed && (
        <div className={styles.chipRow}>
          <button type="button" className={styles.chip} onClick={() => open(failed.name)}>
            <AlertOutlined aria-hidden="true" />{labelOf(failed.name)} · 未通过
          </button>
        </div>
      )}
      {rows.ordered.length ? (
        <ol className={styles.timeline}>
          {rows.ordered.map((step) => {
            const tone = toneForStep(step);
            const tag = TONE_TAG[tone];
            return (
              <li key={`${step.name}-${step.attempt ?? 1}`}>
                <button type="button" className={styles.timeRow} onClick={() => open(step.name)}>
                  <span className={`${styles.timeDot} ${styles[TONE_CLASS[tone]]}`} aria-hidden="true" />
                  <span className={styles.timeMain}>
                    <span className={styles.timeHead}>
                      <span className={styles.timeName}>{labelOf(step.name)}</span>
                      <ToneTag tone={tag.tone}>{tag.text}</ToneTag>
                      <span className={styles.timeDur}>{step.duration_ms != null ? formatDuration(step.duration_ms) : ''}</span>
                    </span>
                    <span className={styles.timeSummary}>{step.assessment?.summary || step.summary || step.error?.message || '未记录摘要'}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <EmptyState
          title="暂无原子步骤记录"
          description="任务可能仍在排队，或者后端尚未落盘步骤执行记录。"
        />
      )}
      <div className={styles.footRow}>
        <span className={styles.footMeta}>
          已记录 {rows.ordered.length} 条执行记录
          {view.terminalStep ? ` · 业务停止点：${labelOf(view.terminalStep)}` : ''}
        </span>
        {view.reviewStep && <Button size="small" type="link" onClick={() => onOpenStep('review_agent')}>审查完整记录</Button>}
      </div>

      {selectedStep && (
        <StepDrawer
          key={selectedStep.name}
          runId={detail.run_id}
          taskId={detail.task.task_id}
          step={selectedStep}
          order={rows.ordered}
          runActive={runActive}
          approvalPending={approvalPending}
          onClose={() => setSelected('')}
          onSelect={setSelected}
          openStepInTabs={onOpenStep}
        />
      )}
    </TaskSection>
  );
}

/** 步骤抽屉：输入/输出/产物 + Agent 调用，支持上一步/下一步与方向键。 */
function StepDrawer({
  runId, taskId, step, order, runActive, approvalPending, onClose, onSelect, openStepInTabs,
}: {
  runId: string;
  taskId: string;
  step: StepRecord;
  order: StepRecord[];
  runActive: boolean;
  approvalPending: boolean;
  onClose: () => void;
  onSelect: (name: string) => void;
  openStepInTabs: (name: string) => void;
}) {
  const index = order.findIndex((item) => item.name === step.name);
  const prev = index > 0 ? order[index - 1] : undefined;
  const next = index >= 0 && index < order.length - 1 ? order[index + 1] : undefined;
  const model = MODEL_STEPS.has(step.name);
  const title = labelOf(step.name);

  const detail = useQuery({
    queryKey: queryKeys.step(runId, taskId, step.name),
    queryFn: () => api.step(runId, taskId, step.name),
    enabled: Boolean(runId && taskId && step.name),
    refetchInterval: 5000,
  });
  const current: StepRecord = detail.data?.step.name === step.name ? detail.data.step : step;

  // 方向键切换：抽屉打开时接管左右键，避免和浏览器前进后退冲突。
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      const target = event.target as HTMLElement | null;
      // 输入框、文本域里保留原生光标移动。
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
      if (event.key === 'ArrowLeft' && prev) { event.preventDefault(); onSelect(prev.name); }
      if (event.key === 'ArrowRight' && next) { event.preventDefault(); onSelect(next.name); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [prev, next, onSelect]);

  const result = current.error?.message || current.assessment?.summary || current.summary
    || (current.status === 'skipped' ? '此步骤已跳过，具体原因见执行记录。'
      : current.status === 'running' ? '正在执行'
        : '尚未产出结果');

  return (
    <WorkbenchDrawer
      title={`${title} · ${step.name}`}
      open
      onClose={onClose}
      width="min(960px, 100vw)"
      footer={(
        <div className={styles.drawerFoot}>
          <Button size="small" icon={<ArrowLeftOutlined />} disabled={!prev} onClick={() => prev && onSelect(prev.name)}>
            上一步
          </Button>
          <span className={styles.drawerPos}>{index >= 0 ? `${index + 1} / ${order.length}` : '—'}</span>
          <span className={styles.drawerHint}>← / → 切换</span>
          <span className={styles.drawerSpacer} />
          <Button size="small" onClick={() => openStepInTabs(step.name)}>在分区中打开</Button>
          <Button size="small" type="primary" icon={<ArrowRightOutlined />} disabled={!next} onClick={() => next && onSelect(next.name)}>
            下一步
          </Button>
        </div>
      )}
    >
      <div className={styles.stepDetail}>
        <div className={styles.stepHead}>
          <StatusBadge kind="step" value={current.status} stepName={current.name} />
          <span className={styles.stepMeta}>
            第 {current.attempt ?? 1} 次 · {formatDuration(current.duration_ms)} · {formatTime(current.finished_at || current.started_at)}
          </span>
        </div>
        <p className={styles.stepResult}>{result}</p>
        <StepAssessment step={current} />
        {detail.isError && <Alert type="error" showIcon message="步骤详情暂时无法加载" description={detail.error instanceof Error ? detail.error.message : '请重试'} />}
        {model && current.status !== 'skipped' && (
          <AgentTrace runId={runId} taskId={taskId} stepName={current.name} attempt={current.attempt} />
        )}
        <Tabs
          size="small"
          items={[
            { key: 'conclusion', label: '结论与证据', children: <StepOutcome step={current} /> },
            { key: 'input', label: '执行输入', children: <JsonDocument value={current.input || {}} filename={`${current.name}-input.json`} /> },
            { key: 'output', label: '执行输出', children: <JsonDocument value={current.output || {}} filename={`${current.name}-output.json`} /> },
            {
              key: 'artifacts',
              label: '产物及上下文',
              children: (
                <>
                  <JsonDocument value={{ artifacts: detail.data?.artifacts || current.artifacts, context: detail.data?.context }} filename={`${current.name}-evidence.json`} />
                  <ContextInspector context={detail.data?.context} />
                </>
              ),
            },
          ]}
        />
        <StepActionBar
          runId={runId}
          taskId={taskId}
          stepName={current.name}
          stepStatus={current.status}
          runActive={runActive}
          approvalPending={approvalPending}
          availability={detail.data?.actions?.rerun}
          continuation={detail.data?.actions?.continue_after}
        />
      </div>
    </WorkbenchDrawer>
  );
}

