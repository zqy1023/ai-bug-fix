import type { Tone } from '../app/theme';

type Segment = { key: string; count: number; tone: Tone; label: string };

/** 任务进度热力条：一段色带扫出整轮成败，是总览页的核心视觉。 */
export function RunProgressHeatmap({
  succeeded, waiting, manual, failed, total, running,
}: {
  succeeded: number; waiting: number; manual: number; failed: number; running: number; total: number;
}) {
  const segments: Segment[] = [
    { key: 'succeeded', count: succeeded, tone: 'success', label: '自动交付' },
    { key: 'waiting', count: waiting, tone: 'waiting', label: '待交付审批' },
    { key: 'running', count: running, tone: 'running', label: '运行中' },
    { key: 'manual', count: manual, tone: 'manual', label: '转人工报告' },
    { key: 'failed', count: failed, tone: 'danger', label: '失败' },
  ];
  const shown = segments.filter((item) => item.count > 0);
  if (!total) {
    return <div className="heatmap is-empty"><span>暂无任务</span></div>;
  }
  return (
    <div className="heatmap" role="img" aria-label={shown.map((item) => `${item.label} ${item.count}`).join('，')}>
      {shown.map((item) => (
        <span
          key={item.key}
          className={`heatmap-cell tone-${item.tone}`}
          style={{ flexGrow: item.count }}
          title={`${item.label} ${item.count} 个任务`}
        >
          {item.count}
        </span>
      ))}
    </div>
  );
}
