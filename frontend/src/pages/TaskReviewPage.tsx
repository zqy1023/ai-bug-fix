import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Card, CardBody, ErrorBox, Skeleton } from '../components/ui';
import { TaskHeader } from '../components/TaskHeader';
import { TaskReview } from '../components/TaskReview';

export function TaskReviewPage() {
  const { runId = '', taskId = '' } = useParams();
  const query = useQuery({ queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId), enabled: Boolean(runId && taskId) });
  if (query.isLoading && !query.data) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (query.error || !query.data) return <Card><ErrorBox error={query.error ?? new Error('任务不存在')} retry={() => void query.refetch()} /></Card>;
  return <><TaskHeader detail={query.data} active="review" /><TaskReview detail={query.data} /></>;
}
