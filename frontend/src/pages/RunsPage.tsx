import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatBatchTime, shortId } from '../lib/format';
import { attentionCount, isRunActive, runProgress } from '../lib/status';
import { useRunEvents } from '../lib/hooks';
import { Card, CardBody, CardHead, Empty, ErrorBox, PageHeader, ProgressBar, RunStatusBadge, Skeleton, Verdict, useToast } from '../components/ui';
import type { RunQuery } from '../lib/types';

export function RunsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState(params.get('keyword') ?? '');
  const [page, setPage] = useState(1);
  const [group, setGroup] = useState<'all' | 'active' | 'ended'>('all');
  const queryClient = useQueryClient();
  const { push } = useToast();

  const query: RunQuery = {
    page,
    page_size: 20,
    status_group: group === 'all' ? undefined : group,
    keyword: params.get('keyword') ?? '',
  };
  const runs = useQuery({ queryKey: ['runs', query], queryFn: () => api.runs(query), refetchInterval: 20_000 });
  useRunEvents(runs.data?.items.find((run) => isRunActive(run.status))?.run_id);
  const newOpen = params.get('new') === '1';

  const openNew = () => setParams((current) => { current.set('new', '1'); return current; });
  const closeNew = () => setParams((current) => { current.delete('new'); return current; });

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setParams((current) => {
      if (keyword.trim()) current.set('keyword', keyword.trim());
      else current.delete('keyword');
      return current;
    });
  };

  const counts = runs.data?.counts;
  const total = runs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / 20));

  const attentionRuns = useMemo(
    () => (runs.data?.items ?? []).filter((run) => attentionCount(run) > 0),
    [runs.data],
  );

  return (
    <>
      <PageHeader
        eyebrow="Runs / 批次"
        title="批次"
        meta={`${counts?.active ?? 0} 个执行中，${counts?.ended ?? 0} 个已结束。`}
        actions={<button className="btn primary" onClick={openNew}>新建批次</button>}
      />

      {attentionRuns.length > 0 ? (
        <Verdict
          tone="warn"
          title={`${counts?.attention ?? 0} 个任务需要你处理`}
          desc={`分布在 ${attentionRuns.length} 个批次；先处理等待最久的 ${attentionRuns[0].run_id.slice(0, 8)}。`}
          actions={<Link className="link" to={`/runs/${attentionRuns[0].run_id}`}>先看最紧急的 →</Link>}
        />
      ) : null}

      {runs.error ? (
        <Card><ErrorBox error={runs.error} retry={() => void runs.refetch()} /></Card>
      ) : (
        <>
          <div className="toolbar">
            <div className="chips">
              <button className={`chip${group === 'all' ? ' active' : ''}`} onClick={() => { setGroup('all'); setPage(1); }}>
                全部 <span className="n">{counts ? counts.active + counts.ended : total}</span>
              </button>
              <button className={`chip${group === 'active' ? ' active' : ''}`} onClick={() => { setGroup('active'); setPage(1); }}>
                进行中 <span className="n">{counts?.active ?? 0}</span>
              </button>
              <button className={`chip${group === 'ended' ? ' active' : ''}`} onClick={() => { setGroup('ended'); setPage(1); }}>
                已结束 <span className="n">{counts?.ended ?? 0}</span>
              </button>
            </div>
            <form className="search" onSubmit={submitSearch}>
              <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m16 16 4 4" /></svg>
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索批次、承运商"
                aria-label="搜索批次"
                style={{ border: 0, outline: 0, background: 'transparent', width: '100%', color: 'inherit' }}
              />
            </form>
          </div>

          <Card>
            <CardHead title="批次列表" sub={<span>共 {total} 个</span>} />
            {runs.isLoading ? (
              <CardBody><Skeleton rows={6} /></CardBody>
            ) : (runs.data?.items.length ?? 0) === 0 ? (
              <Empty
                title="没有匹配的批次"
                desc="调整筛选条件，或新建一个批次。"
                action={<button className="btn primary" onClick={openNew}>新建批次</button>}
              />
            ) : (
              <div className="card-body flush">
                <table className="table">
                  <thead>
                    <tr>
                      <th>批次</th>
                      <th>结论</th>
                      <th>进度</th>
                      <th>需处理</th>
                      <th>时间</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(runs.data?.items ?? []).map((run) => (
                      <tr key={run.run_id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/runs/${run.run_id}`)}>
                        <td>
                          <div className="row-title mono">{shortId(run.run_id, 8)}</div>
                          <div className="row-sub">{run.task_count} 个任务 · {run.operator || 'kirby'}</div>
                        </td>
                        <td><RunStatusBadge status={run.status} /></td>
                        <td>
                          <div className="bar-row">
                            <ProgressBar pct={runProgress(run).pct} tone={attentionCount(run) ? 'warn' : undefined} />
                            <span className="txt">{runProgress(run).done} / {run.task_count}</span>
                          </div>
                        </td>
                        <td className={attentionCount(run) ? 'cell-mono' : 'cell-muted'}>{attentionCount(run) || '—'}</td>
                        <td className="cell-muted nowrap">{formatBatchTime(run.started_at)}</td>
                        <td className="chev">›</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="card-foot">
              <span>第 {page} / {pages} 页</span>
              <div className="row">
                <button className="btn sm" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
                <button className="btn sm" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>下一页</button>
              </div>
            </div>
          </Card>
        </>
      )}

      <NewRunModal
        open={newOpen}
        onClose={closeNew}
        onStarted={(runId) => {
          closeNew();
          void queryClient.invalidateQueries({ queryKey: ['runs'] });
          void queryClient.invalidateQueries({ queryKey: ['dashboard'] });
          push('批次已创建', shortId(runId, 8));
          navigate(`/runs/${runId}`);
        }}
      />
    </>
  );
}

function NewRunModal({
  open,
  onClose,
  onStarted,
}: {
  open: boolean;
  onClose: () => void;
  onStarted: (runId: string) => void;
}) {
  const config = useQuery({ queryKey: ['run-config'], queryFn: api.runConfig, enabled: open });
  const { push } = useToast();
  const [hours, setHours] = useState(12);
  const [limit, setLimit] = useState(0);
  const [attempts, setAttempts] = useState(3);
  const [unifiedModel, setUnifiedModel] = useState('');
  const [reviewModel, setReviewModel] = useState('');

  const defaults = config.data?.defaults;
  const models = config.data?.models ?? {};
  const unifiedOptions = models.unified_fixer ?? config.data?.model_catalog ?? [];
  const reviewOptions = models.review_agent ?? config.data?.model_catalog ?? [];

  const start = useMutation({
    mutationFn: () =>
      api.startRun({
        log_hours: hours,
        log_limit: limit,
        max_attempts: attempts,
        review_mode: 'agent',
        agent_policy: {
          ...(defaults?.agent_policy ?? {}),
          ...(unifiedModel ? { unified_model: unifiedModel } : {}),
          ...(reviewModel ? { review_model: reviewModel } : {}),
        },
      }),
    onSuccess: (result) => onStarted(result.run_id),
    onError: (error) => push('无法创建批次', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  return (
    <div>
      {open ? (
        <NewRunDialog
          configLoading={config.isLoading}
          hours={hours}
          setHours={setHours}
          limit={limit}
          setLimit={setLimit}
          attempts={attempts}
          setAttempts={setAttempts}
          maxAttempts={config.data?.limits.max_attempts ?? 5}
          maxHours={config.data?.limits.max_log_hours ?? 720}
          unifiedModel={unifiedModel}
          setUnifiedModel={setUnifiedModel}
          reviewModel={reviewModel}
          setReviewModel={setReviewModel}
          unifiedOptions={unifiedOptions}
          reviewOptions={reviewOptions}
          submitting={start.isPending}
          onClose={onClose}
          onSubmit={() => start.mutate()}
        />
      ) : null}
    </div>
  );
}

import { Modal, Field, TextInput, Button } from '../components/ui';
import type { ModelOption } from '../lib/types';

function NewRunDialog({
  configLoading,
  hours,
  setHours,
  limit,
  setLimit,
  attempts,
  setAttempts,
  maxAttempts,
  maxHours,
  unifiedModel,
  setUnifiedModel,
  reviewModel,
  setReviewModel,
  unifiedOptions,
  reviewOptions,
  submitting,
  onClose,
  onSubmit,
}: {
  configLoading: boolean;
  hours: number;
  setHours: (value: number) => void;
  limit: number;
  setLimit: (value: number) => void;
  attempts: number;
  setAttempts: (value: number) => void;
  maxAttempts: number;
  maxHours: number;
  unifiedModel: string;
  setUnifiedModel: (value: string) => void;
  reviewModel: string;
  setReviewModel: (value: string) => void;
  unifiedOptions: ModelOption[];
  reviewOptions: ModelOption[];
  submitting: boolean;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const presets = [1, 6, 12, 24];
  return (
    <Modal
      open
      onClose={onClose}
      title="新建批次"
      desc="从真实日志中发现问题，自动分析、修复、审查并交付。一次只执行一个批次。"
      width={660}
      footer={
        <>
          <div className="eta">任务数在日志发现后统计</div>
          <div className="row">
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn primary" disabled={submitting || configLoading} onClick={onSubmit}>
              {submitting ? '正在创建…' : '开始执行'}
            </button>
          </div>
        </>
      }
    >
      <Field label="时间窗口" hint={`读取最近 ${hours} 小时内的日志；上限 ${maxHours} 小时。`}>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          {presets.map((value) => (
            <button key={value} className={`chip${hours === value ? ' active' : ''}`} onClick={() => setHours(value)}>
              {value} 小时
            </button>
          ))}
          <TextInput
            type="number"
            min={1}
            max={maxHours}
            value={hours}
            onChange={(event) => setHours(Math.max(1, Math.min(maxHours, Number(event.target.value) || 1)))}
            style={{ width: 92 }}
          />
        </div>
      </Field>

      <Field label="日志上限" hint="0 表示全量拉取；设置上限可避免一次性拉取过多日志。">
        <TextInput
          type="number"
          min={0}
          value={limit}
          onChange={(event) => setLimit(Math.max(0, Number(event.target.value) || 0))}
        />
      </Field>

      <Field label="最大尝试次数" hint="同一任务最多重试 3 次，含首次执行；审查失败只自动回修一次。">
        <div className="row">
          <Button size="sm" onClick={() => setAttempts(Math.max(1, attempts - 1))}>−</Button>
          <span className="mono" style={{ width: 20, textAlign: 'center' }}>{attempts}</span>
          <Button size="sm" onClick={() => setAttempts(Math.min(maxAttempts, attempts + 1))}>+</Button>
        </div>
      </Field>

      <Field label="模型" hint="来自运行配置；可在设置中查看完整模型目录。">
        <div className="stack-8">
          <div className="row between">
            <span style={{ fontSize: 12.5 }}>统一分析修复</span>
            <select
              className="input mono"
              style={{ width: 240 }}
              value={unifiedModel}
              onChange={(event) => setUnifiedModel(event.target.value)}
            >
              <option value="">使用默认</option>
              {unifiedOptions.map((model) => (
                <option key={model.key} value={model.model_id} disabled={!model.available}>
                  {model.label}{model.available ? '' : '（不可用）'}
                </option>
              ))}
            </select>
          </div>
          <div className="row between">
            <span style={{ fontSize: 12.5 }}>修复后审查</span>
            <select
              className="input mono"
              style={{ width: 240 }}
              value={reviewModel}
              onChange={(event) => setReviewModel(event.target.value)}
            >
              <option value="">使用默认</option>
              {reviewOptions.map((model) => (
                <option key={model.key} value={model.model_id} disabled={!model.available}>
                  {model.label}{model.available ? '' : '（不可用）'}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Field>

      <Field label="高级选项" hint="Agent 策略（共享层、允许新文件、远程推送）；排除消息/业务类型来自环境配置，只读。">
        <p className="note">审查方式固定为 AI 自动审查；人工审批已移除。</p>
      </Field>
    </Modal>
  );
}
