import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Card, CardHead, CardBody, ErrorBox, RawMessage, Skeleton } from '../components/ui';
import { useProblemLog } from '../lib/hooks';
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
  const problem = useProblemLog(detail);

  if (detailQuery.isLoading && !detail) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detailQuery.error || !detail) return <Card><ErrorBox error={detailQuery.error ?? new Error('任务不存在')} /></Card>;

  return (
    <>
      <TaskHeader detail={detail} active="change" />
      <Card><CardHead title="原始异常"/><CardBody>
        {problem.loading ? <Skeleton rows={3}/> : problem.message ? <RawMessage text={problem.message} truncated={problem.truncated}/> :
          <p className="note">{problem.failed ? '原始异常读取失败，请刷新重试。' : '该任务未归档完整原文。'}</p>}
      </CardBody></Card>
      <TaskResults detail={detail} analysis={false} />
      <TaskReview detail={detail} />
      <TaskCodeChanges detail={detail} />
    </>
  );
}
