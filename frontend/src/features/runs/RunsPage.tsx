import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App, Button, Input, Pagination, Select, Skeleton } from 'antd';
import { PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { percent } from '../../lib/format';
import type { RunStatus } from '../../lib/types';
import { KpiStrip } from '../../components/KpiStrip';
import { EmptyState } from '../../components/EmptyState';
import { RunCard } from './RunCard';
import { StartRunModal } from './StartRunModal';

const PAGE_SIZE = 12;

const STATUS_OPTIONS: Array<{ value: RunStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'running', label: '运行中' },
  { value: 'waiting_approval', label: '待交付审批' },
  { value: 'succeeded', label: '已完成' },
  { value: 'partial_failed', label: '部分失败' },
  { value: 'failed', label: '失败' },
  { value: 'paused', label: '已暂停' },
  { value: 'cancelled', label: '已停止' },
];

export function RunsPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<RunStatus | ''>('');
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [startOpen, setStartOpen] = useState(false);

  const dashboard = useQuery({ queryKey: queryKeys.dashboard, queryFn: api.dashboard, refetchInterval: 15000 });
  const runs = useQuery({
    queryKey: queryKeys.runs({ page, status, keyword }),
    queryFn: () => api.runs({ page, page_size: PAGE_SIZE, status: status || undefined, keyword: keyword || undefined }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  });

  const summary = dashboard.data;
  const trendSucceeded = summary?.daily_trend.map((item) => item.succeeded) ?? [];
  const trendFailed = summary?.daily_trend.map((item) => item.failed) ?? [];

  const kpis = [
    { key: 'active', label: '进行中执行', value: summary?.active_runs ?? '—', tone: 'running' as const, hint: '排队、运行与暂停中', trend: trendSucceeded },
    { key: 'approval', label: '待交付审批', value: summary?.waiting_approvals ?? '—', tone: 'waiting' as const, hint: '全流程唯一人工节点' },
    { key: 'success', label: '24h 自动交付', value: summary?.succeeded_24h ?? '—', tone: 'success' as const, hint: '修复分支已产出', trend: trendSucceeded },
    { key: 'failed', label: '24h 失败 / 转人工', value: summary?.failed_24h ?? '—', tone: 'danger' as const, hint: '需人工关注', trend: trendFailed },
    { key: 'rate', label: '7 天成功率', value: summary ? percent(summary.success_rate_7d) : '—', tone: 'neutral' as const, hint: '按任务成功口径' },
  ];

  const applySearch = () => { setKeyword(keywordDraft.trim()); setPage(1); };
  const resetFilters = () => { setStatus(''); setKeyword(''); setKeywordDraft(''); setPage(1); };

  return (
    <div className="page runs-page">
      <div className="page-heading">
        <div>
          <h2>执行阵列</h2>
          <p>每一轮执行是一条自动修复流水线。点开卡片进入 LangGraph 工作区。</p>
        </div>
        <div className="page-heading-actions">
          <Button icon={<ReloadOutlined />} onClick={() => { void runs.refetch(); void dashboard.refetch(); }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setStartOpen(true)}>发起执行</Button>
        </div>
      </div>

      <KpiStrip items={kpis} />

      <div className="filter-bar">
        <Select
          value={status}
          onChange={(value) => { setStatus(value); setPage(1); }}
          options={STATUS_OPTIONS}
          style={{ width: 168 }}
        />
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索 Run ID"
          value={keywordDraft}
          onChange={(event) => setKeywordDraft(event.target.value)}
          onPressEnter={applySearch}
          style={{ width: 260 }}
        />
        <Button onClick={applySearch}>筛选</Button>
        <Button type="text" onClick={resetFilters}>重置</Button>
        <span className="filter-count">共 {runs.data?.total ?? 0} 轮执行</span>
      </div>

      {runs.isError && (
        <EmptyState
          title="执行列表暂时不可用"
          description={runs.error instanceof ApiError ? runs.error.message : '请检查工作流服务连接'}
          action={<Button onClick={() => void runs.refetch()}>重试</Button>}
        />
      )}

      {runs.isLoading && (
        <div className="run-grid">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="run-card is-skeleton"><Skeleton active paragraph={{ rows: 3 }} /></div>
          ))}
        </div>
      )}

      {!runs.isLoading && !runs.isError && (runs.data?.items.length ?? 0) === 0 && (
        <EmptyState
          title="还没有执行记录"
          description="发起一次执行，系统会自动拉取日志、定位问题并交付修复分支。"
          action={<Button type="primary" icon={<PlusOutlined />} onClick={() => setStartOpen(true)}>发起执行</Button>}
        />
      )}

      {!runs.isLoading && !runs.isError && (runs.data?.items.length ?? 0) > 0 && (
        <>
          <div className="run-grid">
            {runs.data!.items.map((run) => (
              <RunCard
                key={run.run_id}
                run={run}
                onOpen={() => {
                  if (run.waiting_tasks > 0) message.info('该执行有待交付审批任务');
                  navigate(`/runs/${encodeURIComponent(run.run_id)}`);
                }}
              />
            ))}
          </div>
          <div className="pagination-row">
            <Pagination
              current={page}
              pageSize={PAGE_SIZE}
              total={runs.data!.total}
              showSizeChanger={false}
              onChange={setPage}
            />
          </div>
        </>
      )}

      <StartRunModal open={startOpen} onClose={() => setStartOpen(false)} />
    </div>
  );
}
