import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Skeleton } from 'antd';
import type { TaskDetailResponse } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';
import { EmptyState } from '../../components/EmptyState';

export interface TaskDiffProps { readonly detail: TaskDetailResponse }
export function TaskDiff({ detail }: TaskDiffProps) {
  const [path, setPath] = useState('');
  const artifact = [...detail.task.steps].reverse().flatMap((step) => step.artifacts || []).concat(detail.artifacts).find((item) => item.name === 'diff' || /[/\\]diff\.json$/.test(item.path));
  const query = useQuery({
    queryKey: [...queryKeys.artifactContent(detail.run_id, artifact?.path || '', detail.task.task_id), detail.delivery?.candidate_id, detail.delivery?.commit],
    queryFn: () => api.artifactContent(detail.run_id, artifact!.path, detail.task.task_id),
    enabled: Boolean(artifact), staleTime: 60_000,
  });
  if (!artifact) return <EmptyState title="尚无代码差异产物" description="变更门禁记录实际差异后，可在这里查看逐行源码对照。" />;
  if (query.isLoading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (query.isError) return <Alert type="error" message="差异产物读取失败" description={query.error.message} />;
  return <CodeDiffViewer payload={query.data?.content} selectedPath={path} onSelectedPathChange={setPath} />;
}
