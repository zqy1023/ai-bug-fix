import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Result, Skeleton } from 'antd';
import { ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { ApprovalQueue } from './ApprovalQueue';
import { ReviewPane } from './ReviewPane';
import { DecisionBar } from './DecisionBar';
import { EmptyState } from '../../components/EmptyState';

/** 交付审批审阅台：流水线唯一的人工门禁。 */
export function ApprovalsPage() {
  const navigate = useNavigate();
  const approvals = useQuery({ queryKey: queryKeys.approvals, queryFn: api.approvals, refetchInterval: 15000 });
  const items = useMemo(() => approvals.data ?? [], [approvals.data]);
  const [selectedId, setSelectedId] = useState('');

  useEffect(() => {
    if (!items.length) {
      if (selectedId) setSelectedId('');
      return;
    }
    if (!items.some((item) => item.approval_id === selectedId)) setSelectedId(items[0].approval_id);
  }, [items, selectedId]);

  const selected = items.find((item) => item.approval_id === selectedId) ?? items[0];

  useEffect(() => {
    if (items.length < 2) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return;
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
    <div className="page approvals-page">
      <div className="page-heading">
        <div>
          <h2>交付审批</h2>
          <p>审查已通过验证、回归与 AI 审查的修复方案；这也是整条流水线唯一的人工节点。</p>
        </div>
        <div className="page-heading-actions">
          <span className="heading-count"><SafetyCertificateOutlined /> 待决策 <strong>{items.length}</strong></span>
          <Button icon={<ReloadOutlined />} loading={approvals.isFetching} onClick={() => void approvals.refetch()}>
            刷新队列
          </Button>
        </div>
      </div>

      {approvals.isLoading ? (
        <div className="approvals-layout">
          <Skeleton active paragraph={{ rows: 8 }} />
          <Skeleton active paragraph={{ rows: 12 }} />
        </div>
      ) : !items.length ? (
        <EmptyState
          title="当前没有待审批交付"
          description="自动修复完成并通过门禁后会出现在这里；也可以直接回到执行阵列观察运行。"
          action={<Button type="primary" onClick={() => navigate('/runs')}>回到执行阵列</Button>}
        />
      ) : (
        <div className="approvals-layout">
          <ApprovalQueue
            items={items}
            selectedId={selected?.approval_id ?? ''}
            loading={approvals.isLoading}
            onSelect={setSelectedId}
          />
          <div className="approvals-review">
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
