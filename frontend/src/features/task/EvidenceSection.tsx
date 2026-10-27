import { Alert, Button, Skeleton } from 'antd';
import type { TaskDetailResponse } from '../../lib/types';
import { useTaskVerification } from '../../hooks/useTaskVerification';
import { VerificationChecklist } from '../../components/VerificationChecklist';
import { ReplayComparison } from './ReplayEvidence';
import type { TaskView } from './taskVerdict';
import { EvidenceBadge, Fold, Section as TaskSection, SectionEmpty, ToneTag } from '../../components/section/Section';
import styles from './TaskSections.module.css';

const REPLAY_EMPTY: Record<string, { title: string; body: string }> = {
  not_executed: { title: '审查步骤未执行', body: '本任务没有进入修复后审查，因此没有重放证据；这不是验证失败结论。' },
  cancelled: { title: '审查已取消', body: '审查被取消，重放证据不会再产生；已产出的记录保留。' },
  skipped: { title: '审查已跳过', body: '审查步骤被跳过，本任务不会产生重放证据。' },
  manual: { title: '审查结论需人工判断', body: '审查结论为人工处理，没有产出完整的双侧重放证据。' },
  failed: { title: '审查未通过', body: '审查未通过，重放证据不完整；请查看审查结论与失败节点。' },
  running: { title: '审查进行中', body: '审查尚未结束，完整重放证据稍后才会出现。' },
  waiting: { title: '审查等待中', body: '审查在等待输入，重放证据尚未产生。' },
};

/** 验证证据：四项检查结论常驻，双侧重放默认展开，审查发现按需折叠。 */
export function EvidenceSection({ detail, view, onOpenStep }: {
  detail: TaskDetailResponse;
  view: TaskView;
  onOpenStep: (name: string) => void;
}) {
  const verification = useTaskVerification(detail);
  const { verification: evidence, review, delivery } = view;
  const empty = REPLAY_EMPTY[verification.stopKind as string] ?? { title: '尚无双侧重放证据', body: '取得同一原始响应的修复前后验证记录后，将在这里展示对照结果。' };

  const reviewTag = review.verdict === 'PASS' ? { tone: 'ok' as const, text: 'AI 审查通过' }
    : review.verdict === 'FAIL' ? { tone: 'bad' as const, text: 'AI 审查未通过' }
      : review.verdict === 'MANUAL' ? { tone: 'warn' as const, text: delivery.analysisOnly ? '报告路径' : '需人工判断' }
        : review.step ? { tone: 'warn' as const, text: review.verdict || '审查已执行' }
          : { tone: 'muted' as const, text: '审查未执行' };

  return (
    <TaskSection
      title="验证证据"
      subtitle={delivery.analysisOnly
        ? '当前分支交付的是分析报告，不进入源码修复审批'
        : 'AI 审查通过后直接交付修复分支；人工复核只在交付后决定保留或打回'}
      extra={<ToneTag tone={reviewTag.tone}>{reviewTag.text}</ToneTag>}
    >
      <div className={styles.evidenceRow}>
        <EvidenceBadge tone={evidence.verifyTone}>问题验证 · {evidence.verifyText}</EvidenceBadge>
        <EvidenceBadge tone={evidence.regressionTone}>真实请求回归 · {evidence.regressionText}</EvidenceBadge>
        <EvidenceBadge tone={evidence.syntaxTone}>语法检查 · {evidence.syntaxText}</EvidenceBadge>
        <EvidenceBadge tone={view.changes.evidenceKnown ? 'ok' : view.changes.checkState === 'not_returned' ? 'warn' : 'muted'}>
          变更门禁 · {view.changes.evidenceKnown ? '基线比对完成' : view.changes.checkState === 'not_returned' ? '未回传' : '未执行'}
        </EvidenceBadge>
      </div>

      {evidence.internalEvidenceMissing && (
        <div className={styles.pad}>
          <div className={`${styles.banner} ${styles.bannerWarn}`} role="status">
            审查内部证据未回传：变更核验、原问题重放或真实请求回归的结果没有随审查结论返回。
            下方“未回传”不代表通过或未通过，请到执行时间线或审查归档产物中核对。
          </div>
        </div>
      )}

      <div className={styles.pad}>
        <div className={styles.subHead}>修复前后重放对比</div>
        {verification.isLoading ? <Skeleton active paragraph={{ rows: 4 }} />
          : verification.error ? <Alert type="warning" showIcon message="完整验证产物读取失败，请从执行时间线重试" />
            : verification.hasComparison ? <ReplayComparison replay={verification.replay} full />
              : <SectionEmpty title={empty.title} body={empty.body} />}
        {verification.step && (
          <div className={styles.footRow}>
            <Button size="small" type="link" onClick={() => onOpenStep(verification.step!.name)}>打开完整重放记录</Button>
          </div>
        )}
      </div>

      <div className={styles.pad}>
        <div className={styles.subHead}>审查模型</div>
        <VerificationChecklist layout="ribbon" steps={detail.task.steps} onOpenStep={onOpenStep} />
      </div>

      <div className={styles.pad}>
        <div className={styles.subHead}>{delivery.analysisOnly ? 'AI 审查结论（报告路径）' : 'AI 审查结论（非最终审批）'}</div>
        {review.step ? (
          <>
            <p className={styles.paragraph}>{review.summary || '审查步骤已执行，但没有记录摘要。'}</p>
            {(review.findings.length > 0 || review.requiredChanges.length > 0) && (
              <Fold summary="展开完整审查内容" count={review.findings.length ? `${review.findings.length} 项发现` : undefined} defaultOpen>
                {review.summary && <div className={styles.block}><label>审查摘要</label><p>{review.summary}</p></div>}
                {review.findings.length > 0 && (
                  <div className={styles.block}><label>审查发现（{review.findings.length}）</label><ul>{review.findings.map((item) => <li key={item}>{item}</li>)}</ul></div>
                )}
                {review.requiredChanges.length > 0 && (
                  <div className={styles.block}><label>需要处理的事项（{review.requiredChanges.length}）</label><ul>{review.requiredChanges.map((item) => <li key={item}>{item}</li>)}</ul></div>
                )}
              </Fold>
            )}
          </>
        ) : (
          <SectionEmpty
            title="审查步骤未执行"
            body="本任务没有 AI 审查结论；任务可能在修复、验证阶段前结束，或处于人工审查模式。"
          />
        )}
      </div>

      <div className={styles.footRow}>
        <Button
          size="small"
          type="link"
          onClick={() => onOpenStep('verify_issue')}
          disabled={!detail.task.steps.some((step) => step.name === 'verify_issue')}
          title={detail.task.steps.some((step) => step.name === 'verify_issue')
            ? '打开原问题重放步骤'
            : '原问题重放记录只存在于审查归档中，请在执行时间线查看'}
        >
          问题验证详情
        </Button>
        <Button
          size="small"
          type="link"
          onClick={() => onOpenStep('run_regression')}
          disabled={!detail.task.steps.some((step) => step.name === 'run_regression')}
          title={detail.task.steps.some((step) => step.name === 'run_regression')
            ? '打开回归测试步骤'
            : '回归记录只存在于审查归档中，请在执行时间线查看'}
        >
          回归详情
        </Button>
        {review.step && <Button size="small" type="link" onClick={() => onOpenStep('review_agent')}>完整审查记录</Button>}
      </div>
    </TaskSection>
  );
}
