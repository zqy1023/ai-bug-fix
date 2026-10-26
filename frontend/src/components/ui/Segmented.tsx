import type { ReactNode } from 'react';
import styles from './ui.module.css';

export interface SegmentOption<T extends string = string> {
  value: T;
  label: ReactNode;
}

/** 分段选择器(替代下拉筛选);选项少且互斥时使用。 */
export function Segmented<T extends string = string>({
  options, value, onChange, ariaLabel,
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div className={styles.seg} role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={`${styles.segBtn}${option.value === value ? ` ${styles.segOn}` : ''}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
