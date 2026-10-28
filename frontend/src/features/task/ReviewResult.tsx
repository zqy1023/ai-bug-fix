import type { StepRecord } from '../../lib/types';
import styles from './ReviewResult.module.css';

const LABELS: Record<string, string> = { passed: '通过', failed: '未通过', unverified: '证据不足', skipped: '旧记录未执行', syntax_only: '仅语法', not_configured: '未配置', not_executed: '未执行' };
type Check = { key: string; label: string; status: string; detail: string };

const VERDICT_CLASS: Record<string, string> = { pass: 'verdictPass', fail: 'verdictFail' };
const STATUS_CLASS: Record<string, string> = { passed: 'statusPassed', failed: 'statusFailed' };

export function ReviewResult({ step }: { step: StepRecord }) {
  const output = (step.output?.payload || step.output || {}) as Record<string, unknown>;
  const verdict = String(output.verdict || step.result || 'UNKNOWN').toUpperCase();
  const checks = Array.isArray(output.checks) ? output.checks as Check[] : [];
  const findings = Array.isArray(output.findings) ? output.findings.map(String) : [];
  const changes = Array.isArray(output.required_changes) ? output.required_changes.map(String) : [];
  const contractError = String(output.contract_error || '');
  const title = contractError ? '审查输出异常' : ({ PASS: '代码审查通过', FAIL: '代码审查未通过', MANUAL: '代码审查存在待判断事项' }[verdict] || '尚无审查结论');
  const dimensions = (output.dimensions || {}) as Record<string, { evidence?: string[] }>;
  const patch = (output.evidence_version || {}) as Record<string, string>;
  return <div className={styles.result}>
    <header className={`${styles.verdict} ${styles[VERDICT_CLASS[verdict.toLowerCase()] ?? 'verdict']}`}>
      <div><strong>{title}</strong><p>{String(output.summary || step.summary || '等待审查结束')}</p></div>
      {patch.patch_id && <code title={patch.patch_id}>补丁 {patch.patch_id.slice(0, 10)}</code>}
    </header>
    {Boolean(output.environment_assessment) && <p><strong>验证环境：</strong>{String(output.environment_assessment)}</p>}
    {Boolean(output.upstream_assessment) && <p><strong>官网状态：</strong>{String(output.upstream_assessment)}</p>}
    {checks.length ? <div className={styles.checkList} aria-label="审查分项结果">
      {checks.map((check) => <div className={styles.check} key={check.key}>
        <strong>{check.label}</strong><span className={`${styles.status} ${styles[STATUS_CLASS[check.status] ?? 'status']}`}>{LABELS[check.status] || '未知'}</span><p>{check.detail}{dimensions[check.key]?.evidence?.map((ref) => <span key={ref}><br /><code>{ref}</code></span>)}</p>
      </div>)}
    </div> : <p className={styles.legacyNote}>旧记录没有分项结果，无法从长段总结可靠推断各项是否通过。重新审查后生成检查表。</p>}
    {contractError && <p role="alert" className={styles.contractError}>协议错误：{contractError}</p>}
    {findings.length > 0 && <section className={styles.notes}><strong>已确认缺陷</strong><ul>{findings.map((value, index) => <li key={index}>{value}</li>)}</ul></section>}
    <section className={styles.nextAction}><strong>待处理事项</strong>
      {changes.length > 0 ? <ul>{changes.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>没有额外处理项</p>}
      <p>已确认缺陷进入回修；交付后由人工确认保留或打回。</p>
    </section>
    {Boolean(output.review_notes) && <details className={styles.notes}><summary>查看完整审查说明</summary><p>{String(output.review_notes)}</p></details>}
  </div>;
}
