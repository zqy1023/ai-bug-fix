import { useState } from 'react';
import { Button, Tabs } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, FileTextOutlined } from '@ant-design/icons';
import { StatusBadge } from '../../components/StatusBadge';
import { Panel } from '../../components/ui';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { JsonDocument } from '../../components/JsonDocument';
import { formatDuration, formatTime } from '../../lib/format';
import { readableRunFailure, type RunPhase } from './runDetailModel';
import styles from './RunPhasePanel.module.css';

export interface RunPhasePanelProps {
  readonly phase: RunPhase;
  readonly onTasks: () => void;
  readonly nextAction?: string;
  readonly blockedBeforeTasks?: boolean;
}

export function RunPhasePanel({ phase, onTasks, nextAction, blockedBeforeTasks }: RunPhasePanelProps) {
  const [evidence, setEvidence] = useState<{ name: string; tab: string } | null>(null);
  const records = phase.records;
  const selected = records.find(step => step.name === evidence?.name);
  return <>
    <Panel title={phase.title} extra={phase.key === 'tasks' && phase.status === 'waiting'
      ? <StatusBadge kind="task" value="WAITING_APPROVAL" /> : <StatusBadge kind="step" value={phase.status} />}>
      {!records.length ? <div className={styles.unstarted}>
        <FileTextOutlined />
        <h3>{phase.key === 'tasks' ? '问题任务独立处理' : phase.status === 'skipped' ? '本轮未执行此阶段' : '等待阶段开始'}</h3>
        <p>{phase.key === 'tasks' ? '进入问题任务查看定位、修复和验证结果。' : phase.status === 'skipped' ? '上游执行停止后，本阶段没有继续执行。' : phase.description}</p>
        {phase.key === 'tasks' && <Button onClick={onTasks}>查看本轮任务</Button>}
      </div> : records.map(step => {
        const failed = step.status === 'failed' || step.assessment?.outcome === 'failed';
        const completed = step.status === 'succeeded' && !failed;
        const raw = step.error?.message || step.summary || '';
        const summary = failed ? readableRunFailure(raw, phase.key === 'source') : step.assessment?.summary || step.summary || '该步骤的处理记录已保存。';
        const measured = step.started_at && step.finished_at ? Date.parse(step.finished_at) - Date.parse(step.started_at) : 0;
        const duration = (step.duration_ms || 0) > 0 ? step.duration_ms : measured > 0 ? measured : undefined;
        return <article key={step.name} className={styles.step}>
          {records.length > 1 && <div className={styles.stepTitle}>{step.title || step.name}</div>}
          <div className={`${styles.result} ${failed ? styles.failed : completed ? styles.completed : styles.neutral}`}>
            {failed ? <CloseCircleOutlined /> : completed ? <CheckCircleOutlined /> : <FileTextOutlined />}
            <div><h3>{failed ? phase.key === 'source' ? '未能建立本轮源码基线' : '该步骤未完成' : completed ? '步骤执行完成' : '步骤状态已记录'}</h3><p>{summary}</p>
              {failed && blockedBeforeTasks && phase.key === 'source' && <p>问题采集和修复尚未开始，本轮没有生成问题任务。</p>}
            </div>
          </div>
          {failed && nextAction && <section className={styles.advice} aria-label="建议处理"><strong>下一步</strong><p>{nextAction}</p></section>}
          {Boolean(step.assessment?.findings?.length) && <details className={styles.findings}><summary>查看审查发现（{step.assessment!.findings!.length}）</summary><ul>{step.assessment!.findings!.map((finding,index) => <li key={index}>{finding}</li>)}</ul></details>}
          <div className={styles.actions}><Button onClick={() => setEvidence({name:step.name,tab:'log'})}>查看完整日志</Button><Button type="text" onClick={() => setEvidence({name:step.name,tab:'input'})}>输入参数</Button><Button type="text" onClick={() => setEvidence({name:step.name,tab:'output'})}>原始输出</Button></div>
          <dl className={styles.facts}><div><dt>开始时间</dt><dd>{formatTime(step.started_at)}</dd></div><div><dt>执行耗时</dt><dd>{formatDuration(duration)}</dd></div><div><dt>执行次数</dt><dd>{step.attempt ?? '未记录'}</dd></div></dl>
        </article>;
      })}
    </Panel>
    <WorkbenchDrawer title={`${phase.title} · 执行记录`} open={Boolean(evidence)} onClose={() => setEvidence(null)} width="min(900px, 100vw)">
      {selected && <><div className={styles.evidenceMeta}><code>{selected.name}</code><span>第 {selected.attempt || 1} 次执行</span></div><Tabs activeKey={evidence?.tab} onChange={tab => setEvidence({name:selected.name,tab})} items={[
        {key:'log',label:'执行日志',children:<pre className={styles.log}>{selected.error?.message || selected.summary || '该步骤没有保存日志正文。'}</pre>},
        {key:'input',label:'输入参数',children:<JsonDocument value={selected.input ?? {}} filename={`${selected.name}-input.json`} />},
        {key:'output',label:'原始输出',children:<JsonDocument value={selected.output ?? {}} filename={`${selected.name}-output.json`} />},
      ]} /></>}
    </WorkbenchDrawer>
  </>;
}
