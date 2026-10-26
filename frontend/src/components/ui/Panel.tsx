import type { ReactNode } from 'react';
import styles from './ui.module.css';

/** 面板原语:标题行 + 可选 extra;body 可选 flush(无缝内容,如表格/列表)。 */
export function Panel({
  title, extra, children, flush = false,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className={styles.panel}>
      {title !== undefined && (
        <div className={styles.panelHead}>
          {title}
          {extra !== undefined && <span className={styles.panelExtra}>{extra}</span>}
        </div>
      )}
      <div className={flush ? styles.flush : styles.panelBody}>{children}</div>
    </section>
  );
}
