import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { TaskDetailResponse } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDuration } from '../../lib/format';

const BAD_RESULTS = new Set(['FAIL', 'FAILED', 'REJECTED']);
const MANUAL_RESULTS = new Set(['MANUAL', 'INSUFFICIENT_EVIDENCE']);

export function TaskHeader({ detail }: { detail: TaskDetailResponse }) {
  const navigate = useNavigate();
  const { task } = detail;
  const report = task.report;
  const attempt = Math.max(0, ...(task.steps ?? []).map((step) => step.attempt ?? 0));
  const result = String(task.result || '').toUpperCase();
  const tone = BAD_RESULTS.has(result) ? 'bad' : MANUAL_RESULTS.has(result) ? 'warn' : result ? 'good' : 'idle';
  const reason = report?.terminal_reason || '';

  return (
    <header className="task-header">
      <div className="task-header-line">
        <Button type="text" size="small" icon={<ArrowLeftOutlined />}
          onClick={() => navigate(`/runs/${encodeURIComponent(detail.run_id)}`)} />
        <h2>{task.carrier || '未识别运输商'}</h2>
        <StatusBadge kind="task" value={task.result} />
        <span className="task-breadcrumb">{detail.run_id}</span>
        <div className="task-header-meta">
          <span>{task.biztype || '未知业务类型'}</span>
          <span>{String(task.problem?.service_name ?? '—')}</span>
          {attempt > 0 && <span>attempt {attempt}</span>}
          <span>{task.steps?.length ?? 0} 步</span>
          <span>{formatDuration(report?.elapsed_ms ?? task.duration_ms)}</span>
        </div>
      </div>

      {(reason || report?.next_action) && (
        <div className={`task-banner is-${tone}`}>
          <div className="task-banner-copy">
            <span className="task-banner-label">
              {BAD_RESULTS.has(result) ? '失败原因' : MANUAL_RESULTS.has(result) ? '人工处理' : '终态原因'}
            </span>
            <p title={reason}>{reason || '—'}</p>
          </div>
          {report?.next_action && (
            <div className="task-banner-next">
              <span>下一步</span>
              <strong>{report.next_action}</strong>
            </div>
          )}
        </div>
      )}

      {MANUAL_RESULTS.has(result) && (
        <span className="outcome-note">分析报告类任务：无需在系统内操作，按下一步线下处理。</span>
      )}
    </header>
  );
}
