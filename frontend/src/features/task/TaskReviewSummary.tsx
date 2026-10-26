import { Alert, Button, Skeleton } from 'antd';
import { FileSearchOutlined } from '@ant-design/icons';
import type { TaskDetailResponse } from '../../lib/types';
import { Panel } from '../../components/ui';
import { VerificationChecklist } from '../../components/VerificationChecklist';
import { ReplayComparison, object } from './ReplayEvidence';
import { useTaskVerification } from '../../hooks/useTaskVerification';
import styles from './TaskReviewSummary.module.css';

interface TaskReviewSummaryProps {
  readonly detail: TaskDetailResponse;
  readonly onOpenStep: (name: string) => void;
}

export function TaskReviewSummary({ detail, onOpenStep }: TaskReviewSummaryProps) {
  const { task } = detail;
  const latest = (name: string) => [...task.steps].reverse().find((step) => step.name === name);
  const locate = latest('locate_agent');
  const locateOutput = object(locate?.output?.payload || locate?.output);
  const verification = useTaskVerification(detail);
  const summary = task.report?.change_summary || locateOutput.summary || locateOutput.root_cause || locate?.summary || task.lifecycle?.terminal_reason || task.report?.terminal_reason;
  const result = String(task.result || '').toUpperCase();
  const stopped = ['MANUAL','INSUFFICIENT_EVIDENCE','SKIPPED','CANCELLED','FAILED','FAIL','BLOCKED'].includes(result);
  const heading = ({MANUAL:'需要人工处理，尚未确认修复完成',INSUFFICIENT_EVIDENCE:'证据不足，需要补充信息',SKIPPED:'本任务已跳过，未执行修复',CANCELLED:'本任务已取消，未执行修复',FAILED:'本次修复未完成',FAIL:'本次修复未完成',BLOCKED:'验证受阻，需要处理阻断原因'} as Record<string,string>)[result];
  const hasReview = task.steps.some(step => step.name === 'review_agent');
  const original = String(task.problem?.error || task.problem?.raw_error || '');
  // 重放证据为什么可能不存在，要按审查的真实停点说明，不能统一写成“尚无证据”。
  const replayEmpty = ({
    not_executed: { title: '审查步骤未执行', body: '本任务没有进入修复后审查，因此没有重放证据；这不是验证失败结论。' },
    cancelled: { title: '审查已取消', body: '审查被取消，重放证据不会再产生；已产出的记录保留。' },
    skipped: { title: '审查已跳过', body: '审查步骤被跳过，本任务不会产生重放证据。' },
    manual: { title: '审查结论需人工判断', body: '审查结论为人工处理，没有产出完整的双侧重放证据。' },
    failed: { title: '审查未通过', body: '审查未通过，重放证据不完整；请查看审查结论与失败节点。' },
    running: { title: '审查进行中', body: '审查尚未结束，完整重放证据稍后才会出现。' },
    waiting: { title: '审查等待中', body: '审查在等待输入，重放证据尚未产生。' },
  } as Record<string, { title: string; body: string }>)[verification.stopKind]
    ?? { title: '尚无双侧重放证据', body: '取得同一原始响应的修复前后验证记录后，将在这里展示对照结果。' };
  return <div className={styles.root}>
    <section className={styles.summary} aria-label="修复概览">
      <div className={styles.kicker}><FileSearchOutlined /> 修复概览</div>
      <h2>{heading || (summary ? String(summary) : task.approval_pending ? (detail.delivery?.phase === 'published' ? '修复分支已交付，等待人工复核' : '本地候选已准备，等待交付与复核') : '跟踪问题定位、修复与交付结果')}</h2>
      <p>{stopped ? '本轮结果以分析报告和停止原因为准，未将步骤执行完成视为修复通过。' : detail.delivery?.phase === 'published' ? '修复分支已交付，提交与分支信息可在任务信息中查看。' : detail.delivery?.phase === 'prepared' ? '当前为本地候选；交付后由人工复核，决定保留或打回。' : '修复结论以步骤的正式业务判定和归档证据为准。'}</p>
      {heading && summary && <details className={styles.error}><summary>分析结论与停止原因</summary><p>{String(summary)}</p></details>}
      {original && <details className={styles.error}><summary>原始异常 <code>{original.split(/\r?\n/)[0]}</code></summary><pre>{original}</pre></details>}
    </section>
    {(!stopped || hasReview) && <div className={styles.checks}><Panel title="修复后代码审查" flush><VerificationChecklist layout="ribbon" steps={task.steps} onOpenStep={onOpenStep} /></Panel></div>}
    <details className={styles.evidence}>
      <summary><strong>修复前后重放对比</strong><span>{verification.isLoading ? '证据读取中' : verification.hasComparison ? '已归档 · 展开查看' : replayEmpty.title}</span></summary>
      <div>{verification.isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : verification.error ? <Alert type="warning" message="完整验证产物读取失败，请从执行记录重试" /> : verification.hasComparison ? <ReplayComparison replay={verification.replay} full /> : <div className={styles.empty}><strong>{replayEmpty.title}</strong><p>{replayEmpty.body}</p></div>}
      {verification.step && <Button type="link" onClick={() => onOpenStep(verification.step!.name)}>打开完整重放记录 →</Button>}</div>
    </details>
  </div>;
}
