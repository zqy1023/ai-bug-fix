import type { ReactNode } from 'react';
import styles from './ui.module.css';

export interface KeyValueItem {
  label: ReactNode;
  value: ReactNode;
  /** 值的语义色,仅用于需要一眼分辨正误的事实项。 */
  tone?: 'ok' | 'bad';
  /** 值为普通文本(不使用等宽字体)时关闭 mono。 */
  text?: boolean;
}

/** 键值事实列表(替代各页面手写 dl 结构)。 */
export function KeyValueList({ items }: { items: KeyValueItem[] }) {
  return (
    <dl className={styles.kvList}>
      {items.map((item, index) => (
        <div className={styles.kvRow} key={index}>
          <dt className={styles.kvLabel}>{item.label}</dt>
          <dd className={`${styles.kvValue}${item.text ? ` ${styles.text}` : ''}${item.tone === 'ok' ? ` ${styles.kvOk}` : item.tone === 'bad' ? ` ${styles.kvBad}` : ''}`}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
