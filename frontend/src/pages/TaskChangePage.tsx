import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Card, CardBody, ErrorBox, Skeleton } from '../components/ui';
import { TaskHeader } from '../components/TaskHeader';
import { TaskResults } from '../components/TaskResults';
import { TaskCodeChanges } from '../components/TaskCodeChanges';
import { TaskReview } from '../components/TaskReview';

export function TaskChangePage() {
  const { runId = '', taskId = '' } = useParams();
  const detailQuery = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
  });
  const detail = detailQuery.data;

  if (detailQuery.isLoading && !detail) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detailQuery.error || !detail) return <Card><ErrorBox error={detailQuery.error ?? new Error('任务不存在')} /></Card>;

  return (
    <>
      <TaskHeader detail={detail} active="change" />
      <TaskResults detail={detail} analysis={false} />
      <TaskReview detail={detail} />
      <TaskCodeChanges detail={detail} />
    </>
  );
}
