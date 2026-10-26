import type { ReactNode } from 'react';
import styles from './ui.module.css';

/** 轻提示横幅:左侧 2px 语义边 + 可选图标/右侧补充信息。 */
export function Banner({
  tone = 'info', icon, children, extra,
}: {
  tone?: 'info' | 'warn' | 'primary';
  icon?: ReactNode;
  children: ReactNode;
  extra?: ReactNode;
}) {
  const toneClass = tone === 'warn' ? styles.bannerWarn : tone === 'primary' ? styles.bannerPrimary : styles.bannerInfo;
  return (
    <div className={`${styles.banner} ${toneClass}`}>
      {icon}
      <span style={{ minWidth: 0 }}>{children}</span>
      {extra !== undefined && <span className={styles.bannerExtra}>{extra}</span>}
    </div>
  );
}
