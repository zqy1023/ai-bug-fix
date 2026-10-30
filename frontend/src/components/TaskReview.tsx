import { Link } from 'react-router-dom';
import { useReview } from '../lib/hooks';
import { lines, object, taskResults, text } from '../lib/taskResults';
import { isReviewFailed, reviewLabel, reviewTone } from '../lib/taskReview';
import type { TaskDetailResponse } from '../lib/types';
import { Badge, Card, CardBody, CardHead } from './ui';

export function TaskReview({ detail }: { detail: TaskDetailResponse }) {
  const review = useReview(detail);
  const results = taskResults(detail);
  const disposition = review?.review_kind === 'analysis_disposition' || (!review?.review_kind && results.files.length === 0);
  const attempt = detail.task.attempt || detail.task.lifecycle?.attempt || 1;
  const step = [...detail.task.steps].reverse().find(item => item.name === 'review_agent' && (item.attempt || 1) === attempt);
  const failed = isReviewFailed(review) || step?.status === 'failed';
  const running = step?.status === 'running' || step?.status === 'waiting';
  const terminal = ['CHANGED_DELIVERED', 'UNCHANGED_DELIVERED', 'FAILED', 'SKIPPED'].includes(detail.task.result);
  const coverage = object(review?.coverage);
  const symbols = lines(coverage.reviewed_symbols);
  const missing = lines(coverage.missing_checks);
  const validation = object(review?.validation);
  const dimensions = review?.dimensions || {};
  const runtimeStates = [text(object(validation.replay).status), text(object(validation.request).status)];
  const runtimeStatus = runtimeStates.every(value => value === 'passed') ? 'passed' : runtimeStates.includes('failed') ? 'failed' : 'unverified';
  const dimensionLabels = disposition ? ([['syntax', '源码与接口核对'], ['business', '分析与不修改依据'], ['scope', '权限与处置范围']] as const)
    : ([['syntax', '语法与接口'], ['business', '异常处理与业务'], ['scope', '改动范围与其他业务']] as const);
  const title = failed ? '审查未完成' : running ? '审查进行中' : review ? `审查${reviewLabel(review.verdict)}` : terminal ? '审查未执行' : '审查未开始';
  const base = `/runs/${detail.run_id}/tasks/${encodeURIComponent(detail.task.task_id)}`;

  return <Card id="review-results" className="task-review">
    <CardHead title={disposition ? '处置审查结果' : '补丁审查结果'} sub={step?.attempt ? `第 ${step.attempt} 次尝试` : undefined}
      actions={<div className="row"><Badge tone={failed ? 'danger' : reviewTone(review?.verdict)}>{title}</Badge>
        <Link className="link" to={`${base}/audit/agent?agent=review`}>查看 Agent 记录 →</Link></div>} />
    <CardBody>
      {!review ? <div className="review-empty"><strong>{title}</strong><p>{running
        ? '审查 Agent 正在核对分析、实际源码与修改依据，完成后会在这里更新判断理由。'
        : failed ? step?.error?.message || step?.summary || '审查步骤失败，尚未取得有效审查结论。'
          : terminal ? '本次记录中没有独立审查结果，不能将任务交付状态视为审查通过。'
            : '等待分析修复阶段完成。审查执行后将展示判断理由、源码范围和实际依据。'}</p></div>
        : <>
          <div className="review-conclusion"><span className="result-label">审查结论</span>
            {disposition ? <p className="note">本轮核对分析和不修改决定；审查通过表示处置有依据，不代表缺陷已修复或业务运行已验证。</p> : null}
            <p className="result-prose">{text(review.summary) || text(review.review_notes) || '本轮记录仅含审查状态，未提供文字结论。'}</p>
            {text(review.contract_error) ? <p className="review-error">{text(review.contract_error)}</p> : null}
          </div>
          <div className="review-decisions">
            <div><span>{disposition ? '处置判断' : '代码判断'}</span><Badge tone={reviewTone(disposition ? review.verdict : review.code_verdict)}>{reviewLabel(disposition ? review.verdict : review.code_verdict)}</Badge></div>
            {disposition ? <div><span>源码修复</span><Badge tone={results.unresolvedIssues.length ? 'warn' : 'idle'}>{results.unresolvedIssues.length ? '缺陷未修复' : '未修改源码'}</Badge></div>
              : <div><span>补丁业务语义</span><Badge tone={review.semantic_verified === true ? 'ok' : 'warn'}>{review.semantic_verified === true ? '已确认' : review.semantic_verified === false ? '未确认' : '未单独记录'}</Badge></div>}
            <div><span>运行验证</span><Badge tone={reviewTone(runtimeStatus)}>{runtimeStatus === 'passed' ? '已验证' : reviewLabel(runtimeStatus)}</Badge></div>
            <div><span>审查覆盖</span><Badge tone={reviewTone(text(coverage.decision))}>{reviewLabel(text(coverage.decision))}</Badge></div>
          </div>
          <div className="review-dimensions">
            {dimensionLabels.map(([key, label]) => {
              const value = object(dimensions[key]);
              return <section className="review-dimension" key={key}>
                <div className="review-section-heading"><h3>{label}</h3><Badge tone={reviewTone(text(value.status))}>{reviewLabel(text(value.status))}</Badge></div>
                <p className="result-prose check-why">{text(value.reason) || '本次未记录该项判断理由。'}</p>
                <ReviewList title="审查依据" items={lines(value.evidence)} empty="未记录具体依据。" />
              </section>;
            })}
          </div>
          <section className="review-coverage">
            <h3>审查了哪些源码与分支</h3>
            <p className="result-prose">{text(coverage.reason) || '本次未记录审查覆盖说明。'}</p>
            <ReviewList title={`已核对位置 · ${symbols.length} 处`} items={symbols} empty="未记录具体源码位置。" code />
            {missing.length ? <ReviewList title="仍缺少的必要检查" items={missing} /> : null}
          </section>
          <div className="review-followups">
            <ReviewList title="阻断本次交付的问题" items={lines(review.findings)} empty={Array.isArray(review.findings) ? '未记录阻断本次交付的审查问题。' : '未提供发现项。'} />
            <ReviewList title="仍未解决的问题" items={results.unresolvedIssues} empty="未单独记录未解决问题；处置通过不表示已有源码修复。" />
            <ReviewList title="必须修改或确认" items={lines(review.required_changes)} empty={Array.isArray(review.required_changes) ? '未记录阻断修改项。' : '未提供回修要求。'} />
            <ReviewList title="TODO · 后续回归建议" items={lines(review.todo)} empty={Array.isArray(review.todo) ? '未记录后续建议。' : '未提供后续建议。'} />
          </div>
          <div className="review-assessments">
            <section><h3>验证环境判断</h3><p className="result-prose">{text(review.environment_assessment) || '未提供环境判断。'}</p></section>
            <section><h3>上游状态判断</h3><p className="result-prose">{text(review.upstream_assessment) || '未提供上游状态判断。'}</p></section>
          </div>
          {Object.keys(validation).length ? <section className="review-validation"><h3>实际执行验证</h3>
            {([['replay', '原始响应回放'], ['request', '真实请求验证']] as const).map(([key, label]) => {
              const check = object(validation[key]);
              return <div key={key}><div className="review-section-heading"><strong>{label}</strong><Badge tone={reviewTone(text(check.status))}>{reviewLabel(text(check.status))}</Badge></div>
                <p className="result-prose">{text(check.reason) || '未提供执行说明。'}</p></div>;
            })}
          </section> : null}
        </>}
    </CardBody>
  </Card>;
}

function ReviewList({ title, items, empty, code = false }: { title: string; items: string[]; empty?: string; code?: boolean }) {
  return <section className={`review-list-section${code ? ' review-symbols' : ''}`}><h4>{title}</h4>
    {items.length ? <ul className="result-list">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
      : empty ? <p className="note">{empty}</p> : null}</section>;
}
