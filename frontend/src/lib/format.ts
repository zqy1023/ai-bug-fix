import dayjs from 'dayjs';

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

export function formatTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD HH:mm:ss') : value;
}

export function formatDayTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD HH:mm') : value;
}

export function formatBatchTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  if (!parsed.isValid()) return value;
  if (parsed.isSame(dayjs(), 'day')) return `今天 ${parsed.format('HH:mm')}`;
  if (parsed.isSame(dayjs().subtract(1, 'day'), 'day')) return `昨天 ${parsed.format('HH:mm')}`;
  return parsed.format('M月D日 HH:mm');
}

export function relativeTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  if (!parsed.isValid()) return value;
  const seconds = dayjs().diff(parsed, 'second');
  if (seconds < 45) return '刚刚';
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} 天前`;
  return parsed.format('MM-DD HH:mm');
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

export function durationFrom(startedAt?: string | null, finishedAt?: string | null): number | null {
  if (!startedAt) return null;
  const start = dayjs(startedAt);
  const end = finishedAt ? dayjs(finishedAt) : dayjs();
  if (!start.isValid() || !end.isValid()) return null;
  return Math.max(0, end.diff(start));
}
