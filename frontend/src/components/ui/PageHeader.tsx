import type { ReactNode } from 'react';
import styles from './ui.module.css';

export interface Crumb {
  label: ReactNode;
  to?: string;
}

/** 页头原语:紧凑标题 + 可选面包屑/说明/计数/操作区(替代各页面自拼 page-heading)。 */
export function PageHeader({
  title, description, crumbs, count, actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  crumbs?: Crumb[];
  count?: { label: string; value: ReactNode };
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHeader}>
      <div className={styles.titleBlock}>
        <h1 className={styles.title}>{title}</h1>
        {crumbs && crumbs.length > 0 && (
          <nav className={styles.crumbs} aria-label="面包屑">
            {crumbs.map((crumb, index) => (
              <span key={index} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                {index > 0 && <span className={styles.sep}>/</span>}
                {crumb.to
                  ? <a href={crumb.to}>{crumb.label}</a>
                  : <span>{crumb.label}</span>}
              </span>
            ))}
          </nav>
        )}
        {description && <div className={styles.desc}>{description}</div>}
      </div>
      <div className={styles.actions}>
        {count && (
          <span className={styles.countChip}>
            {count.label}
            <b>{count.value}</b>
          </span>
        )}
        {actions}
      </div>
    </header>
  );
}
