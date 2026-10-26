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
  const substeps = Array.isArray(output.substeps) ? output.substeps as Array<{name: string; title: string; status: string; summary: string; assessment?: {outcome?: string}}> : [];
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
    {substeps.length > 0 && <details className={styles.notes}><summary>内部检查与执行证据 · {substeps.length} 项</summary>
      <p>以下是审查 Agent 使用的验证工具；最终结论由语法、业务修复和影响范围综合形成。</p>
      <ul>{substeps.map((item) => <li key={item.name}><strong>{item.title}</strong> · {item.status === 'running' ? '执行中' : item.assessment?.outcome === 'passed' ? '证据已取得' : '需综合判断'}<p>{item.summary}</p></li>)}</ul>
      <p>完整记录见本步骤的 review_substeps 归档产物。</p>
    </details>}
    {findings.length > 0 && <section className={styles.notes}><strong>已确认缺陷</strong><ul>{findings.map((value, index) => <li key={index}>{value}</li>)}</ul></section>}
    <section className={styles.nextAction}><strong>待处理事项</strong>
      {changes.length > 0 ? <ul>{changes.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>没有额外处理项</p>}
      <p>后续动作由“确定后续动作”节点统一计算，审查意见不直接触发人工审批。</p>
    </section>
    {Boolean(output.review_notes) && <details className={styles.notes}><summary>查看完整审查说明</summary><p>{String(output.review_notes)}</p></details>}
  </div>;
}
