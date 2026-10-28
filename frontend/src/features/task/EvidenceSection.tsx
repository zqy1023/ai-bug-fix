import { Button } from 'antd';
import type { TaskDetailResponse } from '../../lib/types';
import { VerificationChecklist } from '../../components/VerificationChecklist';
import { ReviewResult } from './ReviewResult';
import type { TaskView } from './taskVerdict';
import { Section as TaskSection, SectionEmpty, ToneTag } from '../../components/section/Section';
import styles from './TaskSections.module.css';

/** 三项检查的唯一来源是审查 Agent 的结论与证据引用。 */
export function EvidenceSection({ detail, view, onOpenStep }: {
  detail: TaskDetailResponse; view: TaskView; onOpenStep: (name: string) => void;
}) {
  const step = view.review.step;
  return <TaskSection title="审查证据" subtitle="由审查 Agent 核对语法、业务修复及影响范围；实际未执行的测试不会标记为已执行。"
    extra={<ToneTag tone={view.review.verdict === 'PASS' ? 'ok' : 'muted'}>{view.review.verdict || '未审查'}</ToneTag>}>
    <div className={styles.pad}>
      <VerificationChecklist layout="ribbon" steps={detail.task.steps} onOpenStep={onOpenStep} />
    </div>
    <div className={styles.pad}>
      {step ? <ReviewResult step={step} /> : <SectionEmpty title="审查步骤未执行" body="分析报告任务可能不进入代码审查；其他任务会在修复后生成三项审查结论。" />}
    </div>
    {step && <div className={styles.footRow}><Button size="small" type="link" onClick={() => onOpenStep('review_agent')}>提示词、工具轨迹与完整输出</Button></div>}
  </TaskSection>;
}
