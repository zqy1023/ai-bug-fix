import type { StepRecord } from '../../lib/types';

const labels: Record<string, string> = { passed: '通过', failed: '不通过', unverified: '无法确认', retry: '自动恢复验证', repair: '回修代码', deliver: '交付分支', blocked: '验证受阻' };

export function ValidationResult({ step }: { step: StepRecord }) {
  const payload = (step.output?.payload || {}) as Record<string, unknown>;
  if (step.name === 'plan_validation') return <section>
    <h3>本次验证范围</h3><p>原始响应双侧重放与真实请求分别验证；仅采用原始录制响应和实际请求结果。</p>
    <p>原始响应：{payload.has_original_response ? '已取得' : '缺失，无法确认原异常修复效果'}</p>
    <ul>{(Array.isArray(payload.changed_symbols) ? payload.changed_symbols : []).map((value) => <li key={String(value)}>{String(value)}</li>)}</ul>
  </section>;
  if (step.name === 'summarize_validation') {
    const dimensions = (payload.dimensions || {}) as Record<string, {status: string; reason: string}>;
    return <section><h3>实际验证结论</h3>{Object.entries(dimensions).map(([name, item]) => <div key={name}>
      <strong>{name === 'issue' ? '原异常修复效果' : '真实请求回归'}：{labels[item.status] || item.status}</strong><p>{item.reason}</p>
    </div>)}{Boolean(payload.stale) && <p role="alert">代码或输入已变化，当前证据需要重新验证。</p>}</section>;
  }
  const decision = (payload.validation_decision || {}) as Record<string, unknown>;
  const counts = (decision.recovery_counts || {}) as Record<string, number>;
  return <section><h3>{labels[String(decision.action)] || '等待流程决定'}</h3><p>{String(decision.reason || '')}</p>
    {Boolean(decision.target) && <p>自动执行：{String(decision.target)}</p>}
    {decision.action === 'retry' && <p>第 {(counts[String(decision.target)] || 0) + 1} 次验证恢复；代码修复为第 {Number(decision.repair_attempt)} 次尝试。</p>}
    {decision.action === 'blocked' && <p>已保留通过项和执行记录；排除上述原因后重新执行，无需批准未验证代码。</p>}
  </section>;
}
