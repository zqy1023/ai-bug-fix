import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { filePatch } from '../lib/diff';
import type { TaskDetailResponse } from '../lib/types';
import { Card, CardBody, CardHead, ErrorBox, Icon, Skeleton } from './ui';
import { FileDiff } from './FileDiff';

export function TaskCodeChanges({ detail, compact = false }: { detail: TaskDetailResponse; compact?: boolean }) {
  const { run_id: runId, task } = detail;
  const diff = useQuery({ queryKey: ['task-changes', runId, task.task_id],
    queryFn: () => api.taskChanges(runId, task.task_id), enabled: Boolean(runId && task.task_id) });
  const files = diff.data?.files ?? [];
  const counts = files.reduce((sum, file) => {
    for (const line of filePatch(file).split('\n')) {
      if (line.startsWith('+') && !line.startsWith('+++')) sum.added++;
      if (line.startsWith('-') && !line.startsWith('---')) sum.deleted++;
    }
    return sum;
  }, { added: 0, deleted: 0 });
  const declared = task.report.changed_files ?? [];
  const unchanged = task.result === 'UNCHANGED_DELIVERED';
  return <Card id="code-changes" className="task-code-changes">
    <CardHead title="实际代码差异" sub={diff.data?.available ? <span className="diff-totals">{files.length} 个文件 <b>+{counts.added}</b><em>−{counts.deleted}</em></span> : undefined}
      actions={compact ? <Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(task.task_id)}/change`}>打开改动详情 →</Link> : undefined} />
    <CardBody>
      {diff.isLoading ? <Skeleton rows={3} /> : diff.error ? <ErrorBox error={diff.error} retry={() => void diff.refetch()} />
        : diff.data?.available && files.length ? <><p className="diff-caption">对比本轮只读基线与实际源码，以下为真实文件差异。</p>{files.map(file => <FileDiff key={file.path} file={file} />)}</>
          : diff.data?.available || unchanged || task.result === 'PENDING' ? <div className="no-code-change"><Icon name="file" size={22} /><div><strong>{unchanged ? '本次仅交付分析报告，未交付源码改动' : task.result === 'PENDING' ? '任务未开始，尚无代码差异' : '当前源码与基线一致'}</strong><p>{unchanged ? '问题原因与处理建议见上方分析结论。' : '生成实际修改后，这里会显示修改前后的内容。'}</p></div></div>
            : <div className="note"><p>{diff.data?.reason || '源码差异暂不可用。'}</p>{declared.map(file => <p className="result-file" key={file}>{file}</p>)}</div>}
    </CardBody>
  </Card>;
}
