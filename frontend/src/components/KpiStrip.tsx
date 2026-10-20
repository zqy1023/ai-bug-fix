import type { Tone } from '../app/theme';

export type KpiItem = {
  key: string;
  label: string;
  value: string | number;
  hint?: string;
  tone?: Tone;
  trend?: number[];
};

function Spark({ values, tone }: { values?: number[]; tone?: Tone }) {
  if (!values?.length) return null;
  const max = Math.max(...values, 1);
  const points = values
    .map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 64},${22 - (value / max) * 18}`)
    .join(' ');
  return (
    <svg className={`kpi-spark tone-${tone ?? 'neutral'}`} viewBox="0 0 64 24" preserveAspectRatio="none" aria-hidden>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

/** 指标带：数字优先，趋势只做背景信息，不做独立图表区。 */
export function KpiStrip({ items }: { items: KpiItem[] }) {
  return (
    <div className="kpi-strip">
      {items.map((item) => (
        <div key={item.key} className={`kpi-item tone-${item.tone ?? 'neutral'}`}>
          <span className="kpi-label">{item.label}</span>
          <div className="kpi-value-line">
            <strong className="kpi-value">{item.value}</strong>
            <Spark values={item.trend} tone={item.tone} />
          </div>
          {item.hint && <span className="kpi-hint">{item.hint}</span>}
        </div>
      ))}
    </div>
  );
}
