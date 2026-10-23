import type { StepRecord } from '../../lib/types';

const LABELS: Record<string, string> = { passed: '通过', failed: '未通过', unverified: '未验证', skipped: '旧记录未执行', syntax_only: '仅语法', not_configured: '未配置', not_executed: '未执行' };
type Check = { key: string; label: string; status: string; detail: string };

export function ReviewResult({ step }: { step: StepRecord }) {
  const output = (step.output || {}) as Record<string, unknown>;
  const verdict = String(output.verdict || step.result || 'UNKNOWN').toUpperCase();
  const checks = Array.isArray(output.checks) ? output.checks as Check[] : [];
  const findings = Array.isArray(output.findings) ? output.findings.map(String) : [];
  const changes = Array.isArray(output.required_changes) ? output.required_changes.map(String) : [];
  const contractError = String(output.contract_error || '');
  const title = contractError ? '审查输出异常' : ({ PASS: '代码审查通过', FAIL: '代码审查未通过', MANUAL: '代码审查存在待判断事项' }[verdict] || '尚无审查结论');
  const patch = (output.evidence_version || {}) as Record<string, string>;
  return <div className="review-result">
    <header className={`review-verdict is-${verdict.toLowerCase()}`}>
      <div><strong>{title}</strong><p>{String(output.summary || step.summary || '等待审查结束')}</p></div>
      {patch.patch_id && <code title={patch.patch_id}>补丁 {patch.patch_id.slice(0, 10)}</code>}
    </header>
    {checks.length ? <div className="review-check-list" aria-label="审查分项结果">
      {checks.map((check) => <div className="review-check" key={check.key}>
        <strong>{check.label}</strong><span className={`review-check-status is-${check.status}`}>{LABELS[check.status] || '未知'}</span><p>{check.detail}</p>
      </div>)}
    </div> : <p className="review-legacy-note">旧记录没有分项结果，无法从长段总结可靠推断各项是否通过。重新审查后生成检查表。</p>}
    {contractError && <p role="alert" className="review-contract-error">协议错误：{contractError}</p>}
    {findings.length > 0 && <section className="review-notes"><strong>已确认缺陷</strong><ul>{findings.map((value, index) => <li key={index}>{value}</li>)}</ul></section>}
    <section className="review-next-action"><strong>待处理事项</strong>
      {changes.length > 0 ? <ul>{changes.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>没有额外处理项</p>}
      <p>后续动作由“确定后续动作”节点统一计算，审查意见不直接触发人工审批。</p>
    </section>
    {Boolean(output.review_notes) && <details className="review-notes"><summary>查看完整审查说明</summary><p>{String(output.review_notes)}</p></details>}
  </div>;
}
