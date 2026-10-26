import type { ReactNode } from 'react';
import styles from './ui.module.css';

export interface StatItem {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  /** hint 的语义色;仅趋势等少数场景使用,不给每个数字上色。 */
  hintTone?: 'ok' | 'bad';
}

/** 无卡片 KPI 数字带:一条带分隔线的统计组(替代逐个 boxed 卡片)。 */
export function StatBand({ stats }: { stats: StatItem[] }) {
  return (
    <div className={styles.statBand}>
      {stats.map((stat, index) => (
        <div className={styles.stat} key={index}>
          <div className={styles.statLabel}>{stat.label}</div>
          <div className={styles.statValue}>{stat.value}</div>
          {stat.hint !== undefined && (
            <div className={`${styles.statHint}${stat.hintTone === 'ok' ? ` ${styles.hintOk}` : stat.hintTone === 'bad' ? ` ${styles.hintBad}` : ''}`}>{stat.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
}
