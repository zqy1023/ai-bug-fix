import type { ReactNode } from 'react';
import { RightOutlined } from '@ant-design/icons';
import styles from './Section.module.css';

/** 语义色调：与设计令牌一一对应，只做辅助编码，文字始终给出语义。 */
export type Tone = 'ok' | 'bad' | 'warn' | 'info' | 'muted';

/** 分区外壳：标题 + 副标题 + 右侧状态，内容自管内边距。任务详情与交付审批共用。 */
export function Section({
  id, title, subtitle, extra, children, label,
}: {
  id?: string;
  title: string;
  subtitle?: string;
  extra?: ReactNode;
  children: ReactNode;
  label?: string;
}) {
  return (
    <section className={styles.section} id={id} aria-label={label ?? title}>
      <header className={styles.head}>
        <div className={styles.titleStack}>
          <h3>{title}</h3>
          {subtitle && <span className={styles.sub}>{subtitle}</span>}
        </div>
        {extra !== undefined && <span className={styles.extra}>{extra}</span>}
      </header>
      {children}
    </section>
  );
}

/** 统一折叠块：只在需要时展开，标出条目数，避免用户误以为默认内容就是全部。 */
export function Fold({
  summary, count, children, tone = 'normal', defaultOpen = false,
}: {
  summary: ReactNode;
  count?: string;
  children: ReactNode;
  tone?: 'normal' | 'warn';
  defaultOpen?: boolean;
}) {
  return (
    <details className={`${styles.fold}${tone === 'warn' ? ` ${styles.foldWarn}` : ''}`} open={defaultOpen}>
      <summary>
        <RightOutlined className={styles.chevron} aria-hidden="true" />
        <span className={styles.foldLabel}>{summary}</span>
        {count && <span className={styles.foldCount}>{count}</span>}
      </summary>
      <div className={styles.foldBody}>{children}</div>
    </details>
  );
}

/** 证据徽标：六态（通过/未通过/未验证/已回传/未回传/未执行）统一成一处呈现。 */
export function EvidenceBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  const toneClass = ({
    ok: styles.evOk, bad: styles.evBad, warn: styles.evWarn, info: styles.evInfo, muted: styles.evMuted,
  })[tone];
  return <span className={`${styles.evidence} ${toneClass}`}><span className={styles.evDot} aria-hidden="true" />{children}</span>;
}

/** 分区内的状态标签（非交互）。 */
export function ToneTag({ tone, children }: { tone: Tone; children: ReactNode }) {
  const toneClass = ({
    ok: styles.tagOk, bad: styles.tagBad, warn: styles.tagWarn, info: styles.tagInfo, muted: styles.tagMuted,
  })[tone];
  return <span className={`${styles.tag} ${toneClass}`}>{children}</span>;
}

/** 空态：分区内一句话说明“为什么没有内容”，不用整块插图。 */
export function SectionEmpty({ title, body }: { title: string; body: string }) {
  return (
    <div className={styles.empty}>
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}
