import { useState } from 'react';
import { Button } from 'antd';
import type { StepRecord, WorkflowStage } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDuration } from '../../lib/format';
import styles from './WorkflowMap.module.css';

export interface WorkflowMapProps {
  readonly steps: StepRecord[];
  readonly stages?: WorkflowStage[];
  readonly selected: string;
  readonly onInspect: (name: string, evidence?: string) => void;
}

/** Execution projection, not a fabricated trace of unobserved branch transitions. */
export function WorkflowMap({ steps, stages = [], selected, onInspect }: WorkflowMapProps) {
  const [name, setName] = useState(selected || steps.at(-1)?.name || '');
  const current = steps.find(step => step.name === name) || steps.at(-1);
  return <section className={styles.root} aria-label="编排状态机">
    <header><div><h2>编排状态机</h2><p>已记录 {steps.length} 个节点。选择节点查看结果，输入和输出按需打开。</p></div></header>
    <ol className={styles.nodes}>{steps.map((step,index) => <li key={step.name}>
      <button type="button" aria-pressed={current?.name === step.name} onClick={() => setName(step.name)}>
        <span className={styles.index}>{String(index+1).padStart(2,'0')}</span>
        <span className={styles.label}><strong>{step.title || step.name}</strong><small>{stages.find(stage => stage.members.includes(step.name))?.title || step.name}</small></span>
        <StatusBadge kind="step" value={step.status} stepName={step.name} />
      </button>
    </li>)}</ol>
    {current ? <article className={styles.detail}>
      <div><strong>{current.title || current.name}</strong><code>{current.name}</code></div>
      <p>{current.assessment?.summary || current.summary || '该节点没有记录处理摘要。'}</p>
      <div className={styles.meta}><span>第 {current.attempt || 1} 次执行</span><span>{formatDuration(current.duration_ms)}</span><span>业务判定：{({passed:'通过',failed:'未通过',inconclusive:'尚无定论',not_applicable:'不适用'} as Record<string,string>)[current.assessment?.outcome || ''] || '未记录'}</span></div>
      <div className={styles.actions}><Button size="small" onClick={() => onInspect(current.name)}>查看节点记录</Button><Button size="small" onClick={() => onInspect(current.name,'input')}>查看输入</Button><Button size="small" onClick={() => onInspect(current.name,'output')}>查看输出</Button></div>
    </article> : <p className={styles.empty}>尚无执行节点记录。</p>}
    <footer>按已落盘记录展示执行顺序；未执行的条件分支不计为失败。</footer>
  </section>;
}
