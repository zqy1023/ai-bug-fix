import { useNavigate } from 'react-router-dom';
import { Button, App } from 'antd';
import { ArrowLeftOutlined, CopyOutlined } from '@ant-design/icons';
import type { TaskDetailResponse } from '../../lib/types';

export function TaskHeader({ detail }: { detail: TaskDetailResponse }) {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { task } = detail;
  const serviceName = String(task.problem?.service_name ?? '');
  const taskId = task.task_id || '';

  const copyTaskId = async () => {
    if (!taskId) return;
    try {
      await navigator.clipboard.writeText(taskId);
      message.success('任务 ID 已复制');
    } catch {
      message.warning('复制失败，请从完整任务 ID 中手动复制');
    }
  };

  return (
    <header className="task-header">
      <div className="task-breadcrumb-row">
        <Button
          type="text"
          size="small"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(`/runs/${encodeURIComponent(detail.run_id)}`)}
        >
          返回运行批次
        </Button>
        <span className="task-breadcrumb-divider">/</span>
        <span className="task-run-ref" title={detail.run_id}>运行批次 <code>{detail.run_id}</code></span>
      </div>
      <div className="task-identity-line">
        <div className="task-identity-main">
          <h1>{task.carrier || '未识别承运商'}</h1>
          <div className="task-identity-meta">
            {task.biztype && <span className="task-identity-chip">{task.biztype}</span>}
            {serviceName && <span>{serviceName}</span>}
          </div>
        </div>
        <Button className="task-id-copy" type="default" size="small" icon={<CopyOutlined />} onClick={() => void copyTaskId()}>
          复制任务 ID
        </Button>
      </div>
    </header>
  );
}
