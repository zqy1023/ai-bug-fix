import { Alert, Button, Skeleton } from 'antd';
import { FileSearchOutlined } from '@ant-design/icons';
import type { TaskDetailResponse } from '../../lib/types';
import { Panel } from '../../components/ui';
import { VerificationChecklist } from '../../components/VerificationChecklist';
import { ReplayComparison, object } from './ReplayEvidence';
import { ValidationResult } from './ValidationResult';
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
  const validation = latest('summarize_validation');
  const summary = task.report?.change_summary || locateOutput.summary || locateOutput.root_cause || locate?.summary || task.lifecycle?.terminal_reason || task.report?.terminal_reason;
  const result = String(task.result || '').toUpperCase();
  const stopped = ['MANUAL','INSUFFICIENT_EVIDENCE','SKIPPED','FAILED','FAIL','BLOCKED'].includes(result);
  const heading = ({MANUAL:'需要人工处理，尚未确认修复完成',INSUFFICIENT_EVIDENCE:'证据不足，需要补充信息',SKIPPED:'本任务已跳过，未执行修复',FAILED:'本次修复未完成',FAIL:'本次修复未完成',BLOCKED:'验证受阻，需要处理阻断原因'} as Record<string,string>)[result];
  const hasReview = task.steps.some(step => step.name === 'review_agent');
  const original = String(task.problem?.error || task.problem?.raw_error || '');
  return <div className={styles.root}>
    <section className={styles.summary} aria-label="修复概览">
      <div className={styles.kicker}><FileSearchOutlined /> 修复概览</div>
      <h2>{heading || (summary ? String(summary) : task.approval_pending ? '本地候选已准备，等待核验与审批' : '跟踪问题定位、修复与交付结果')}</h2>
      <p>{stopped ? '本轮结果以分析报告和停止原因为准，未将步骤执行完成视为修复通过。' : detail.delivery?.phase === 'published' ? '正式交付已完成，提交与分支信息可在任务信息中查看。' : detail.delivery?.phase === 'prepared' ? '当前为本地候选；审批通过后，系统将重新核验并执行正式交付。' : '修复结论以步骤的正式业务判定和归档证据为准。'}</p>
      {heading && summary && <details className={styles.error}><summary>分析结论与停止原因</summary><p>{String(summary)}</p></details>}
      {original && <details className={styles.error}><summary>原始异常 <code>{original.split(/\r?\n/)[0]}</code></summary><pre>{original}</pre></details>}
    </section>
    {(!stopped || hasReview) && <div className={styles.checks}><Panel title="修复后代码审查" flush><VerificationChecklist layout="ribbon" steps={task.steps} onOpenStep={onOpenStep} /></Panel></div>}
    <details className={styles.evidence}>
      <summary><strong>修复前后重放对比</strong><span>{verification.isLoading ? '证据读取中' : verification.hasComparison ? '已归档 · 展开查看' : '尚无双侧证据'}</span></summary>
      <div>{verification.isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : verification.error ? <Alert type="warning" message="完整验证产物读取失败，请从执行记录重试" /> : verification.hasComparison ? <ReplayComparison replay={verification.replay} full /> : <div className={styles.empty}><strong>尚无双侧重放证据</strong><p>取得同一原始响应的修复前后验证记录后，将在这里展示对照结果。</p></div>}
      {verification.step && <Button type="link" onClick={() => onOpenStep(verification.step!.name)}>打开完整重放记录 →</Button>}</div>
    </details>
    {validation && <details className={styles.evidence}><summary><strong>验收断言与详细结论</strong><span>展开查看</span></summary><div><ValidationResult step={validation} /></div></details>}
  </div>;
}
