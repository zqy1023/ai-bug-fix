import { useMemo } from 'react';
import { AlertOutlined, ArrowRightOutlined, RobotOutlined, DiffOutlined, GitlabOutlined, SafetyCertificateOutlined, ExperimentOutlined } from '@ant-design/icons';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDuration } from '../../lib/format';
import { buildHeroTiles, type HeroTileKey, type VerdictTone, type TaskView } from './taskVerdict';
import styles from './TaskVerdictBar.module.css';

const TILE_ICON: Record<HeroTileKey, React.ReactNode> = {
  changes: <DiffOutlined />,
  verify: <SafetyCertificateOutlined />,
  regression: <ExperimentOutlined />,
  review: <RobotOutlined />,
  delivery: <GitlabOutlined />,
};

function toneClass(tone: VerdictTone): string {
  return ({
    ok: styles.toneOk, bad: styles.toneBad, warn: styles.toneWarn, info: styles.toneInfo, muted: styles.toneMuted,
  })[tone];
}

/**
 * 常驻结论条：结论一句话 + 五个证据指标位 + 停止原因/下一步。
 * 指标位同时是分区入口，B 方案里等价于页签切换，避免用户先读结论再找页面。
 */
export function TaskVerdictBar({
  view, activeStepLabel, onOpenTab,
}: {
  view: TaskView;
  activeStepLabel: string;
  onOpenTab: (tab: 'root' | 'changes' | 'evidence' | 'delivery' | 'timeline') => void;
}) {
  const tiles = useMemo(() => buildHeroTiles(view), [view]);
  const { verdict } = view;
  const runningLine = view.active
    ? `当前节点：${activeStepLabel}${view.attempt > 0 ? ` · 第 ${view.attempt} 次尝试` : ''}。较早尝试的错误不会覆盖当前执行状态。`
    : view.paused
      ? `节点 ${activeStepLabel} 的单步执行已结束；当前 Run 没有自动继续。请按依赖顺序选择下一节点执行，或通过 Run 控制重新恢复整轮。`
      : verdict.description;

  return (
    <section className={`${styles.bar} ${styles[`bar${verdict.tone[0].toUpperCase()}${verdict.tone.slice(1)}`] ?? ''}`} aria-label="任务结论">
      <div className={styles.head}>
        <h2 className={styles.title}>{view.active
          ? view.verdict.badge === '执行中' && String(view.result) === 'RETRYING' ? '任务重试中' : verdict.headline
          : verdict.headline}</h2>
        <StatusBadge kind="task" value={view.active ? 'RUNNING' : view.paused ? 'PAUSED' : view.recoveryFailed ? 'FAILED' : view.delivery.finalApprovalPending ? 'WAITING_APPROVAL' : view.result} />
        <span className={styles.meta}>
          <span>耗时 <b>{formatDuration(view.durationMs)}</b></span>
          <span>步骤 <b>{view.stepCount} 条</b></span>
          {view.attempt > 0 && <span>尝试 <b>第 {view.attempt} 次</b></span>}
        </span>
      </div>
      <p className={styles.desc}>{runningLine}</p>

      {verdict.stopReason && (
        <div className={`${styles.notice} ${view.recoveryFailed || view.result === 'FAILED' || view.result === 'FAIL' || view.verification.qualityFailed ? styles.noticeBad : styles.noticeMuted}`} role="status">
          <AlertOutlined aria-hidden="true" />
          <span><b>{verdict.stopLabel}</b>{verdict.stopReason}</span>
        </div>
      )}
      {verdict.nextAction && (
        <div className={`${styles.notice} ${styles.noticeInfo}`} role="status">
          <ArrowRightOutlined aria-hidden="true" />
          <span><b>下一步</b>{verdict.nextAction}</span>
        </div>
      )}

      <div className={styles.tiles} role="group" aria-label="证据指标，点击打开对应分区">
        {tiles.map((tile) => (
          <button
            key={tile.key}
            type="button"
            className={styles.tile}
            onClick={() => onOpenTab(tile.tab)}
            title={`${tile.label}：${tile.value}（${tile.sub}）`}
          >
            <span className={styles.tileLabel}>{TILE_ICON[tile.key]}{tile.label}</span>
            <span className={`${styles.tileValue} ${toneClass(tile.tone)}`}>{tile.value}</span>
            <span className={styles.tileSub}>{tile.sub}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
