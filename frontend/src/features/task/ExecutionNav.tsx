import { useState } from 'react';
import { Input, Segmented } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { StepRecord, WorkflowStage } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDuration } from '../../lib/format';
import { FAILED_RESULTS, TASK_STAGES } from './stages';

export function ExecutionNav({ steps, selected, onSelect, stages = TASK_STAGES }: {
  steps: StepRecord[]; selected: string; onSelect: (name: string) => void; stages?: WorkflowStage[];
}) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('key');
  const keySteps = new Set(['locate_agent', 'repair_agent', 'check_changes', 'verify_issue', 'run_regression', 'review_agent', 'prepare_delivery', 'request_approval', 'finalize_execution']);
  const abnormal = (item: StepRecord) => item.status === 'failed' || item.status === 'waiting'
    || FAILED_RESULTS.has(String(item.result || '').toUpperCase());
  const visible = steps.filter((item) => (
    filter === 'all' || (filter === 'issues' ? abnormal(item) : keySteps.has(item.name) || abnormal(item) || item.name === selected)
  ) && `${item.title} ${item.name}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <nav className="execution-nav" aria-label="执行节点">
    <div className="execution-nav-tools">
      <Input size="small" prefix={<SearchOutlined />} allowClear placeholder="搜索节点" aria-label="搜索执行节点" value={search} onChange={(event) => setSearch(event.target.value)} />
      <Segmented block size="small" value={filter} onChange={setFilter} options={[{ value: 'key', label: `关键节点 ${steps.filter((item) => keySteps.has(item.name) || abnormal(item) || item.name === selected).length}` }, { value: 'all', label: `全部 ${steps.length}` }, { value: 'issues', label: `需关注 ${steps.filter(abnormal).length}` }]} />
    </div>
    <ol className="execution-node-list">
      {visible.map((item) => <li key={item.name}>
        <button type="button" aria-current={selected === item.name ? 'step' : undefined} className={selected === item.name ? 'is-selected' : ''} onClick={() => onSelect(item.name)}>
          <span className="execution-node-number">{String(steps.indexOf(item) + 1).padStart(2, '0')}</span>
          <div><strong>{item.title || item.name}</strong><small>{stages.find((stage) => stage.members.includes(item.name))?.title || '执行步骤'} · {formatDuration(item.duration_ms)}</small></div>
          {['UNVERIFIED', 'MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(String(item.result || '').toUpperCase()) || FAILED_RESULTS.has(String(item.result || '').toUpperCase())
            ? <StatusBadge kind="task" value={String(item.result)} compact />
            : <StatusBadge kind="step" value={item.status} stepName={item.name} compact />}
        </button>
      </li>)}
    </ol>
    {!visible.length && <p className="execution-nav-empty">没有匹配节点</p>}
  </nav>;
}
