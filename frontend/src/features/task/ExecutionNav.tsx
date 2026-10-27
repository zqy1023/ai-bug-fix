import { useEffect, useRef } from 'react';
import type { StepRecord, WorkflowStage } from '../../lib/types';
import { TASK_STAGES } from './stages';
import { FAILED_RESULTS } from './stages';
import styles from './ExecutionNav.module.css';

/** 服务端阶段优先；旧记录使用生成的契约，多步骤阶段可循环查看成员。 */
export interface ExecutionNavProps {
  readonly steps: StepRecord[];
  readonly selected: string;
  readonly onSelect: (name: string) => void;
  readonly stages?: WorkflowStage[];
  readonly vertical?: boolean;
}
export function ExecutionNav({ steps, selected, onSelect, stages, vertical }: ExecutionNavProps) {
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const rail = nav.current;
    const current = rail?.querySelector<HTMLElement>('[aria-current]');
    if (rail && current) rail.scrollLeft = current.offsetLeft - rail.offsetLeft - (rail.clientWidth - current.offsetWidth) / 2;
  }, [selected, stages]);
  const byName = new Map(steps.map((item) => [item.name, item]));
  // 业务判定失败/证据不足与执行失败同档标红;判定色只做辅助编码。
  const abnormal = (item: StepRecord) => item.assessment?.outcome === 'failed' || item.assessment?.outcome === 'inconclusive'
    || item.status === 'failed'
    || FAILED_RESULTS.has(String(item.result || '').toUpperCase());

  return (
    <nav ref={nav} className={`${styles.nav} ${vertical ? styles.vertical : ''}`} aria-label="执行阶段">
      {(stages?.length ? stages : TASK_STAGES).map((stage, index) => {
        const records = stage.members
          .map((name) => byName.get(name))
          .filter((item): item is StepRecord => Boolean(item));
        const failedRecord = records.find(abnormal);
        const activeRecord = records.find((item) => item.status === 'running' || item.status === 'waiting');
        const done = !failedRecord && records.length > 0
          && records.every((item) => item.status === 'succeeded' || item.status === 'skipped');
        const representative = failedRecord ?? activeRecord ?? records[0];
        const target = representative?.name ?? stage.members[0];
        const isCurrent = stage.members.includes(selected);
        const memberIndex = Math.max(0, stage.members.indexOf(selected));
        const stateClass = failedRecord ? styles.stageFailed : activeRecord?.status === 'waiting' ? styles.stageWaiting : done ? styles.stageDone : '';
        return (
          <button
            key={stage.key}
            type="button"
            title={stage.members.join(' → ')}
            aria-current={isCurrent ? 'step' : undefined}
            className={`${styles.stage} ${stateClass}${isCurrent ? ` ${styles.stageCurrent}` : ''}`}
            onClick={() => {
              if (isCurrent && stage.members.length > 1) {
                onSelect(stage.members[(memberIndex + 1) % stage.members.length]);
              } else {
                onSelect(target);
              }
            }}
          >
            <span className={styles.dot} aria-hidden="true">{done ? '✓' : activeRecord?.status === 'waiting' ? '◷' : index + 1}</span>
            {stage.title}
            {stage.members.length > 1 && (
              <span className={styles.count}>
                {(isCurrent ? memberIndex : Math.max(0, stage.members.indexOf(target))) + 1}/{stage.members.length}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
