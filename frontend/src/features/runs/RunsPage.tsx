import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Dropdown, Input, Popover, Radio, Skeleton } from 'antd';
import { ArrowRightOutlined, CloseOutlined, ExclamationCircleOutlined, FilterOutlined, LeftOutlined, PlusOutlined, RightOutlined, SearchOutlined, CalendarOutlined, DownOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useRunsList, type RunListView } from '../../hooks/useRunsList';
import { workbenchQueries } from '../../lib/workbenchQueries';
import type { RunStatus } from '../../lib/types';
import { EmptyState } from '../../components/EmptyState';
import { RunCard } from './RunCard';
import { StartRunModal } from './StartRunModal';
import styles from './RunsPage.module.css';

const PAGE_SIZE = 12;
const STATUS_OPTIONS: Array<{ value: RunStatus | ''; label: string }> = [
  { value: '', label: '全部状态' }, { value: 'running', label: '执行中' },
  { value: 'waiting_approval', label: '待审批' }, { value: 'succeeded', label: '已完成' },
  { value: 'partial_failed', label: '部分失败' }, { value: 'failed', label: '失败' },
  { value: 'paused', label: '已暂停' }, { value: 'cancelled', label: '已停止' },
];
const TIME_RANGES = [{ value: 0, label: '所有时间' }, { value: 1, label: '最近 24 小时' }, { value: 7, label: '最近 7 天' }, { value: 30, label: '最近 30 天' }];
const PRIMARY_VIEWS = [{ value: '' as const, label: '全部批次' }, { value: 'active' as const, label: '执行中' }, { value: 'ended' as const, label: '已结束' }];

export function RunsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<RunListView>('');
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [range, setRange] = useState(7);
  const [expandedId, setExpandedId] = useState<string | null>();
  const [dismissedAttention, setDismissedAttention] = useState('');
  const [startOpen, setStartOpen] = useState(false);
  const from = useMemo(() => range ? new Date(Date.now() - range * 86_400_000).toISOString() : undefined, [range]);
  useEffect(() => {
    const timer = setTimeout(() => { setKeyword(keywordDraft.trim()); setPage(1); setExpandedId(undefined); }, 300);
    return () => clearTimeout(timer);
  }, [keywordDraft]);
  const dashboard = useQuery({ queryKey: queryKeys.dashboard, queryFn: api.dashboard, refetchInterval: 15000 });
  const list = useRunsList({ page, pageSize: PAGE_SIZE, view: status, keyword, from });
  const { query: runs, items, total, attentionCount, attentionRun } = list;
  const attention = useQuery({ ...workbenchQueries.run(attentionRun?.run_id || ''), enabled: Boolean(attentionRun) });
  const attentionTask = attention.data?.tasks?.find((task) => ['BLOCKED','FAILED','FAIL','MANUAL','INSUFFICIENT_EVIDENCE'].includes(task.result));
  const attentionReason = attentionTask?.lifecycle?.terminal_reason || attentionTask?.report?.terminal_reason || attention.data?.run_error?.message;
  const attentionKey = `${attentionRun?.run_id || ''}:${attentionCount}:${attentionReason || ''}`;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => { if (runs.data && page > pages) setPage(pages); }, [runs.data, page, pages]);
  const currentExpanded = expandedId === undefined ? items[0]?.run_id : expandedId;
  const changeStatus = (next: RunListView) => { setStatus(next); setPage(1); setExpandedId(undefined); };
  const openAttention = () => {
    if (!attentionRun) return;
    navigate(attentionTask ? `/runs/${encodeURIComponent(attentionRun.run_id)}/tasks/${encodeURIComponent(attentionTask.task_id)}` : `/runs/${encodeURIComponent(attentionRun.run_id)}`);
  };
  const reset = () => { setKeywordDraft(''); setKeyword(''); changeStatus(''); setRange(7); };

  return <div className={`page ${styles.root}`}>
    <header className={styles.heading}>
      <div><h1>执行批次</h1><p>跟踪异常采集、修复验证与交付决策流</p></div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setStartOpen(true)}>新建批次</Button>
    </header>

    <div className={styles.summary} role="group" aria-label="批次统计">
      <span><i className={styles.runningDot} /><span>执行中</span><b>{dashboard.data?.active_runs ?? '—'}</b></span>
      <span><i className={styles.waitingDot} /><span>待审批</span><b className={styles.waitingValue}>{dashboard.data?.waiting_approvals ?? '—'}</b></span>
      <span title="所选时间与检索范围内，失败、验证受阻和转人工的任务数"><i className={styles.failedDot} /><span>需处理</span><b className={styles.failedValue}>{runs.data ? attentionCount : '—'}</b></span>
    </div>

    {attentionCount > 0 && dismissedAttention !== attentionKey && <div className={styles.attention} role="status">
      <ExclamationCircleOutlined />
      <p title={attentionReason || '存在需要处理的任务'}><strong>{attentionCount} 个任务需要处理：</strong>{attentionReason || '存在执行失败、验证受阻或需要人工判断的任务，请核对执行证据。'}</p>
      <button type="button" className={styles.attentionLink} onClick={openAttention}>查看原因 <ArrowRightOutlined /></button>
      <button type="button" className={styles.dismiss} aria-label="忽略本次关注提示" onClick={() => setDismissedAttention(attentionKey)}><CloseOutlined /></button>
    </div>}

    <div className={styles.toolbar}>
      <div className={styles.views} role="tablist" aria-label="批次视图">
        {PRIMARY_VIEWS.map((view) => <button key={view.value} type="button" role="tab" aria-selected={status === view.value} onClick={() => changeStatus(view.value)}>{view.label}{view.value && <span className={styles.viewCount}> ({view.value === 'active' ? list.activeCount : list.endedCount})</span>}</button>)}
      </div>
      <div className={styles.filters}>
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索批次 ID…" aria-label="搜索批次 ID" value={keywordDraft} onChange={(event) => setKeywordDraft(event.target.value)} onPressEnter={() => { setKeyword(keywordDraft.trim()); setPage(1); }} className={styles.search} />
        <Dropdown trigger={['click']} placement="bottomRight" menu={{ selectedKeys: [String(range)], items: TIME_RANGES.map((item) => ({ key: String(item.value), label: item.label })), onClick: ({key}) => {setRange(Number(key));setPage(1);setExpandedId(undefined);} }}>
          <Button className={styles.range} aria-label="批次时间范围" icon={<CalendarOutlined />}>{TIME_RANGES.find((item) => item.value === range)?.label}<DownOutlined /></Button>
        </Dropdown>
        <Popover trigger="click" placement="bottomRight" title="筛选批次" content={<div className={styles.moreFilters}><span>执行状态</span><div role="radiogroup" aria-label="详细执行状态"><Radio.Group value={status} options={STATUS_OPTIONS} onChange={(event) => changeStatus(event.target.value)} /></div><Button onClick={reset}>重置筛选</Button></div>}>
          <Button type={status && !PRIMARY_VIEWS.some((view) => view.value === status) ? 'primary' : 'default'} icon={<FilterOutlined />} aria-label="更多筛选项" title="更多筛选项" />
        </Popover>
      </div>
    </div>

    {runs.isError ? <EmptyState title="执行列表暂时不可用" description={runs.error instanceof ApiError ? runs.error.message : '请检查工作流服务连接'} action={<Button onClick={() => void runs.refetch()}>重试</Button>} /> : runs.isLoading ? <div className={styles.tablePanel}>{Array.from({ length: 4 }).map((_, index) => <div key={index} className={styles.skeletonRow}><Skeleton active title={false} paragraph={{rows:1,width:'65%'}} /></div>)}</div> : items.length === 0 ? <EmptyState title={keyword || status ? '没有匹配的执行批次' : '还没有执行记录'} description="调整筛选条件或新建一次执行批次。" action={<Button onClick={reset}>重置筛选</Button>} /> : <section className={styles.tablePanel} aria-label="执行批次列表">
      <div className={styles.tableWrap}><table className={styles.table}>
        <thead><tr><th>批次名称 / ID</th><th>状态</th><th>任务进度</th><th>待审批</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>{items.map((run) => <RunCard key={run.run_id} run={run} expanded={currentExpanded === run.run_id} onToggle={() => setExpandedId(currentExpanded === run.run_id ? null : run.run_id)} onOpen={() => navigate(`/runs/${encodeURIComponent(run.run_id)}`)} />)}</tbody>
      </table></div>
      <footer className={styles.tableFoot}>
        <span>共 <b>{total}</b> 个批次{runs.isFetching && <small> · 同步中</small>}</span>
        <div><span>第 {page} / {pages} 页</span><Button size="small" aria-label="上一页批次" icon={<LeftOutlined />} disabled={page <= 1} onClick={() => {setPage(page-1);setExpandedId(undefined);}} /><Button size="small" aria-label="下一页批次" icon={<RightOutlined />} disabled={page >= pages} onClick={() => {setPage(page+1);setExpandedId(undefined);}} /></div>
      </footer>
    </section>}
    <StartRunModal open={startOpen} onClose={() => setStartOpen(false)} />
  </div>;
}
