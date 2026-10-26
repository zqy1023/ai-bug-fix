import { workbenchQueries } from '../../lib/workbenchQueries';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Result, Skeleton } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../lib/api';
import { ApprovalQueue } from './ApprovalQueue';
import { ReviewPane } from './ReviewPane';
import { DecisionBar } from './DecisionBar';
import { EmptyState } from '../../components/EmptyState';
import styles from './ApprovalsPage.module.css';

/** 交付审批审阅台：流水线唯一的人工门禁。 */
export function ApprovalsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const approvals = useQuery(workbenchQueries.approvals());
  const items = useMemo(() => approvals.data ?? [], [approvals.data]);
  const [selectedId, setSelectedId] = useState('');

  useEffect(() => {
    if (!items.length) {
      if (selectedId) setSelectedId('');
      return;
    }
    if (!items.some((item) => item.approval_id === selectedId)) {
      const requested = items.find((item) => params.get('task') ? item.task_id === params.get('task') : params.get('run') ? item.run_id === params.get('run') : false);
      setSelectedId((requested || items[0]).approval_id);
    }
  }, [items, selectedId, params]);

  const selected = items.find((item) => item.approval_id === selectedId) ?? items[0];

  useEffect(() => {
    if (items.length < 2) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [role="combobox"], [contenteditable="true"]')) return;
      event.preventDefault();
      const index = items.findIndex((item) => item.approval_id === selectedId);
      const next = (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      setSelectedId(items[next].approval_id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [items, selectedId]);

  if (approvals.isError) {
    const unauthorized = approvals.error instanceof ApiError && approvals.error.status === 401;
    return (
      <div className="page">
        <Result
          status={unauthorized ? '403' : 'error'}
          title={unauthorized ? '需要工作台访问令牌' : '审批队列暂时不可用'}
          subTitle={approvals.error instanceof ApiError ? approvals.error.message : '请检查工作流服务连接'}
          extra={<Button onClick={() => void approvals.refetch()}>重试</Button>}
        />
      </div>
    );
  }

  return (
    <div className={`page ${styles.root}`}>
      <header className={styles.heading}>
        <div><h1>交付审批</h1><p>AI 修复已直接交付到修复分支；请核对改动与验证证据，决定保留还是打回撤回。</p></div>
        <div><span className={styles.count}>待审批任务 <b>{items.length}</b></span><Button aria-label="刷新审批队列" icon={<ReloadOutlined />} loading={approvals.isFetching} onClick={() => void approvals.refetch()}>刷新队列</Button></div>
      </header>

      {approvals.isLoading ? (
        <div className={styles.layout}>
          <Skeleton active paragraph={{ rows: 8 }} />
          <Skeleton active paragraph={{ rows: 12 }} />
        </div>
      ) : !items.length ? (
        <EmptyState
          title="当前没有待审批交付"
          description="自动修复完成并交付到修复分支后会出现在这里；也可以直接回到执行批次观察运行。"
          action={<Button type="primary" onClick={() => navigate('/runs')}>回到执行批次</Button>}
        />
      ) : (
        <div className={styles.layout}>
          <aside className={styles.queue} aria-label="审批任务队列"><ApprovalQueue
            items={items}
            selectedId={selected?.approval_id ?? ''}
            loading={approvals.isLoading}
            onSelect={setSelectedId}
          /></aside>
          <div className={styles.review} aria-label="审批审阅工作区">
            {selected && <ReviewPane key={selected.approval_id} item={selected} />}
            {selected && (
              <DecisionBar
                key={`decision-${selected.approval_id}`}
                item={selected}
                onDecided={() => setSelectedId('')}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
