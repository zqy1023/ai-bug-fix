import dayjs from 'dayjs';

/** 把毫秒压成运维可读的时长；未知值返回占位符而不是 0。 */
export function formatDuration(ms?: number | null): string {
  if (ms === undefined || ms === null || Number.isNaN(Number(ms))) return '—';
  const value = Math.max(0, Math.round(Number(ms)));
  if (value < 1000) return `${value}ms`;
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** 本地时区展示，tooltip 保留原始 ISO。 */
export function formatTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD HH:mm:ss') : value;
}

/** Compact batch timestamps; the full ISO remains available on the cell title. */
export function formatBatchTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  if (!parsed.isValid()) return value;
  if (parsed.isSame(dayjs(), 'day')) return `今天 ${parsed.format('HH:mm')}`;
  if (parsed.isSame(dayjs().subtract(1, 'day'), 'day')) return `昨天 ${parsed.format('HH:mm')}`;
  return parsed.format('M月D日 HH:mm');
}

export function formatTokens(value?: number | null): string {
  if (value === undefined || value === null) return '—';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function shortId(value?: string | null, size = 8): string {
  if (!value) return '—';
  return value.length <= size ? value : value.slice(0, size);
}

export function percent(value?: number | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '—';
  return `${Math.round(value * 100)}%`;
}
