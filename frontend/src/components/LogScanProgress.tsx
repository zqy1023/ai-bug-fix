import { durationFrom, formatDuration, formatTime, relativeTime } from '../lib/format';
import type { RunDetail } from '../lib/types';

const METRICS = [
  ['es_hits', '已读取日志'], ['parsed', '已解析日志'], ['normalized_count', '已整理日志'],
  ['duplicate_count', '重复日志'], ['unique_count', '去重异常组'],
  ['unparseable', '无法解析'], ['ignored_count', '已过滤日志'],
] as const;

export function LogScanProgress({ run }: { run: RunDetail }) {
  const lifecycle = run.run_lifecycle;
  const progress = lifecycle?.progress;
  if (progress?.step !== 'normalize_error') return null;
  const counts = progress.counts ?? {};
  const step = run.steps.find((item) => item.name === 'normalize_error');
  const complete = step?.status === 'succeeded';
  const scanning = !run.terminal && run.status === 'running' && !complete;
  const limit = counts.record_limit;
  const value = (key: string) => Number.isFinite(counts[key]) ? counts[key].toLocaleString('zh-CN') : '—';
  return (
    <section className="log-scan-progress" aria-label="日志拉取进度">
      <div className="log-scan-heading">
        <strong>{scanning ? '正在拉取并整理日志' : complete ? '日志整理完成' : '日志整理进度'}</strong>
        <span className="cell-muted">{limit === 0 ? '全量扫描 · 总条数未知' : limit > 0 ? `解析条数上限 ${value('record_limit')}` : '扫描总条数未知'}</span>
      </div>
      <dl className="log-scan-metrics" aria-live="polite" aria-atomic="true">
        {METRICS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd className="mono">{value(key)}</dd></div>)}
      </dl>
      <div className="log-scan-meta cell-muted">
        <span>整理耗时 {formatDuration(durationFrom(step?.started_at, step?.finished_at ?? (scanning ? undefined : lifecycle?.progress_at)))}</span>
        <span title={lifecycle?.progress_at}>最近更新 {formatTime(lifecycle?.progress_at)} · {relativeTime(lifecycle?.progress_at)}</span>
      </div>
      {counts.limit_reached ? <p className="note">已达到条数上限，本批次仅包含部分日志。</p> : null}
      {scanning ? <p className="note">扫描完成后生成任务清单；异常组数量尚未最终确定。</p> : null}
    </section>
  );
}
