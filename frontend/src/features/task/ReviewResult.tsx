import type { StepRecord } from '../../lib/types';

const LABELS: Record<string, string> = { passed: '通过', failed: '未通过', unverified: '未验证', skipped: '旧记录未执行', syntax_only: '仅语法', not_configured: '未配置', not_executed: '未执行' };
type Check = { key: string; label: string; status: string; detail: string };

export function ReviewResult({ step }: { step: StepRecord }) {
  const output = (step.output || {}) as Record<string, unknown>;
  const verdict = String(output.verdict || step.result || 'UNKNOWN').toUpperCase();
  const checks = Array.isArray(output.checks) ? output.checks as Check[] : [];
  const findings = Array.isArray(output.findings) ? output.findings.map(String) : [];
  const changes = Array.isArray(output.required_changes) ? output.required_changes.map(String) : [];
  const requirements = Array.isArray(output.validation_requirements) ? output.validation_requirements as Record<string, unknown>[] : [];
  const contractError = String(output.contract_error || '');
  const legacyReview = Number(output.review_contract_version || 0) < 3;
  const title = contractError ? '审查输出异常' : ({ PASS: '审查通过', FAIL: '审查未通过', MANUAL: '需要人工确认' }[verdict] || '尚无审查结论');
  const patch = (output.evidence_version || {}) as Record<string, string>;
  return <div className="review-result">
    <header className={`review-verdict is-${verdict.toLowerCase()}`}>
      <div><strong>{title}</strong><p>{String(output.summary || step.summary || '等待审查结束')}</p></div>
      {patch.patch_id && <code title={patch.patch_id}>补丁 {patch.patch_id.slice(0, 10)}</code>}
    </header>
    {legacyReview && <p className="evidence-warning" role="alert">历史审查记录：原判定为 {verdict}。该记录未采用完整的业务边界及变更验证要求规则，批准前需要重新审查或人工确认。</p>}
    {checks.length ? <div className="review-check-list" aria-label="审查分项结果">
      {checks.map((check) => <div className="review-check" key={check.key}>
        <strong>{check.label}</strong><span className={`review-check-status is-${check.status}`}>{LABELS[check.status] || '未知'}</span><p>{check.detail}</p>
      </div>)}
    </div> : <p className="review-legacy-note">旧记录没有分项结果，无法从长段总结可靠推断各项是否通过。重新审查后生成检查表。</p>}
    {contractError && <p role="alert" className="review-contract-error">协议错误：{contractError}</p>}
    {requirements.length > 0 && <section className="review-notes"><strong>变更验证条件与证据</strong>
      <ul>{requirements.map((item, index) => <li key={String(item.id || index)}>
        <strong>{LABELS[String(item.status)] || '未验证'}</strong> · <code>{String(item.path || '')}:{String(item.symbol || '')}</code>
        <p>{String(item.requirement || '')}</p><p>{String(item.reason || '')}</p>
      </li>)}</ul>
    </section>}
    {findings.length > 0 && <section className="review-notes"><strong>已确认缺陷</strong><ul>{findings.map((value, index) => <li key={index}>{value}</li>)}</ul></section>}
    <section className="review-next-action"><strong>待处理事项</strong>
      {changes.length > 0 ? <ul>{changes.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>没有额外处理项</p>}
      <p>流程动作：{String(output.next_action || (verdict === 'MANUAL' ? '补齐验证材料后复核，或由人工确认候选修改' : '查看具体审查结论'))}</p>
    </section>
    {Boolean(output.review_notes) && <details className="review-notes"><summary>查看完整审查说明</summary><p>{String(output.review_notes)}</p></details>}
  </div>;
}
