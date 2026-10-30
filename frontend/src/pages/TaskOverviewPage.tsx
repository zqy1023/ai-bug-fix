import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Link, useParams } from 'react-router-dom';
import { formatDuration, formatTime } from '../lib/format';
import { taskStatus } from '../lib/status';
import { useProblemLog, useReview } from '../lib/hooks';
import { isReviewFailed } from '../lib/taskReview';
import {
  Badge,
  Card,
  CardBody,
  CardHead,
  CopyButton,
  ErrorBox,
  RawMessage,
  Skeleton,
  Verdict,
} from '../components/ui';
import { TaskHeader } from '../components/TaskHeader';
import { TaskProgress } from '../components/TaskProgress';
import { TaskResults } from '../components/TaskResults';
import { TaskCodeChanges } from '../components/TaskCodeChanges';
import { TaskReview } from '../components/TaskReview';
import type { ReviewPayload, TaskDetailResponse } from '../lib/types';

export function TaskOverviewPage() {
  const { runId = '', taskId = '' } = useParams();
  const detailQuery = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
  });

  if (detailQuery.isLoading && !detailQuery.data) {
    return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  }
  if (detailQuery.error || !detailQuery.data) {
    return <Card><ErrorBox error={detailQuery.error ?? new Error('任务不存在')} retry={() => void detailQuery.refetch()} /></Card>;
  }

  return <TaskOverview detail={detailQuery.data} />;
}

function TaskOverview({ detail }: { detail: TaskDetailResponse }) {
  const { runId = '', taskId = '' } = useParams();
  const problem = useProblemLog(detail);
  const review = useReview(detail);
  const report = detail.task.report;
  const state = taskStatus(detail.task.result, detail.task.lifecycle?.status);
  const delivery = detail.delivery;
  const verdict = verdictOf(detail, review);


  return (
    <>
      <TaskHeader detail={detail} active="overview" />
      <TaskProgress task={detail.task} stages={detail.stages} />

      <Verdict
        tone={verdict.tone}
        title={verdict.title}
        desc={verdict.desc}
        icon={detail.task.result === 'CHANGED_DELIVERED' ? '±' : detail.task.result === 'UNCHANGED_DELIVERED' ? '≡' : '!'}
        actions={<span className="cell-muted">{formatDuration(detail.task.duration_ms ?? report.elapsed_ms)}</span>}
      />

      <TaskResults detail={detail} />
      <TaskReview detail={detail} />
      <TaskCodeChanges detail={detail} compact />

      <Card>
        <CardHead
          title="异常原文"
          sub={
            <span>
              完整 message
              {problem.message ? <span> · {problem.message.length.toLocaleString()} 字符</span> : null}
              {problem.fromArchive ? <span> · 已从无损归档还原</span> : null}
            </span>
          }
          actions={
            problem.message ? (
              <div className="row">
                <CopyButton text={problem.message} label="复制原文" />
                <Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit/steps`}>打开审计 →</Link>
              </div>
            ) : undefined
          }
        />
        <CardBody>
          {problem.loading ? (
            <Skeleton rows={4} />
          ) : problem.message ? (
            <>
              <RawMessage text={problem.message} truncated={problem.truncated} />
              <div className="row between" style={{ marginTop: 8 }}>
                <span className="cell-muted">{problem.truncated ? '原始归档缺失，当前仍为裁剪记录' : problem.fromArchive ? '已从原始记录恢复；未截断' : '来自原始任务记录；未截断'}</span>
              </div>
              {problem.relatedMessages.length ? <details className="related-log">
                <summary>同次请求关联日志（{problem.relatedMessages.length} 条，保留触发日志原文）</summary>
                {problem.relatedMessages.map((message, index) => <RawMessage key={index} text={message} />)}
              </details> : null}
              {problem.extracted ? <details className="related-log"><summary>解析摘要（用于检索）</summary><pre className="raw">{problem.extracted}</pre></details> : null}
            </>
          ) : (
            <p className="note">{problem.failed ? '原始异常读取失败，请刷新重试。' : '这条记录没有保存完整 message。'}</p>
          )}
        </CardBody>
      </Card>

      <div className="layout">
        <div>
          <Card>
            <CardHead title="交付" actions={<Badge tone={deliveryTone(delivery)}>{deliveryLabel(delivery)}</Badge>} />
            <CardBody>
              {delivery ? (
                <dl className="kv">
                  <dt>阶段</dt><dd>{delivery.phase || '—'}</dd>
                  <dt>分支</dt><dd className="mono">{delivery.branch || '—'}</dd>
                  <dt>提交</dt><dd className="mono">{delivery.commit || '—'}</dd>
                  <dt>推送</dt><dd>{delivery.pushed ? '已推送' : '未推送'}</dd>
                  <dt>内容</dt><dd>{delivery.analysis_only ? '仅报告' : '代码修复'}</dd>
                  {delivery.report_paths?.length ? <><dt>报告</dt><dd className="mono">{delivery.report_paths.join(', ')}</dd></> : null}
                </dl>
              ) : (
                <p className="note">未生成交付；审查未通过或任务未走到交付阶段。</p>
              )}
            </CardBody>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="任务信息" />
            <CardBody>
              <dl className="kv">
                <dt>承运商</dt><dd>{detail.task.carrier}</dd>
                <dt>业务</dt><dd>{detail.task.biztype}</dd>
                <dt>服务</dt><dd>{detail.task.problem?.service_name || '—'}</dd>
                <dt>问题码</dt><dd className="mono">{detail.task.task_id.split('__').pop()}</dd>
                <dt>尝试</dt><dd>{state.label === '未开始' ? '未开始' : `第 ${detail.task.attempt || detail.task.lifecycle?.attempt || 1} 次`}</dd>
                <dt>批次</dt><dd className="mono">{detail.run_id}</dd>
                <dt>结束</dt><dd>{report.finished_at ? formatTime(report.finished_at) : '—'}</dd>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHead title="下一步" />
            <CardBody>
              <p className="note">{report.next_action || detail.task.lifecycle?.next_action || '等待处理。'}</p>
              {state.label === '需人工' ? <p className="note" style={{ marginTop: 8 }}>重新调查会创建新的独立批次，保留当前报告与补丁。</p> : null}
            </CardBody>
          </Card>

          <Card>
            <CardHead title="产物" sub={`${detail.artifacts.length} 个`} actions={<Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(taskId)}/audit/artifacts`}>查看全部</Link>} />
            <CardBody>
              {detail.artifacts.slice(0, 4).map((artifact) => (
                <div className="row between" key={`${artifact.path}-${artifact.task_id}`} style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                  <span style={{ fontSize: 12 }}>{artifact.name}</span>
                  <span className="tag">{artifact.media_type?.split('/').pop()?.toUpperCase() || 'JSON'}</span>
                </div>
              ))}
              {detail.artifacts.length === 0 ? <p className="note">暂无任务产物。</p> : null}
            </CardBody>
          </Card>
        </aside>
      </div>
    </>
  );
}

function verdictOf(detail: TaskDetailResponse, review: ReviewPayload | null) {
  const state = taskStatus(detail.task.result, detail.task.lifecycle?.status);
  const reason = detail.task.report.terminal_reason || detail.task.lifecycle?.terminal_reason || '';
  if (state.label === '未开始') {
    return { tone: 'idle' as const, title: '任务未开始', desc: detail.run_status === 'cancelled' ? '批次已取消，此任务尚未执行。' : '任务已加入本批次清单，等待前序任务完成后执行。' };
  }
  if (state.label === '已改动交付' || state.label === '无改动交付') {
    return { tone: state.label === '无改动交付' ? 'info' as const : 'ok' as const, title: state.label,
      desc: state.label === '无改动交付' ? '本次仅交付分析报告，未交付源码修改。处理依据见下方问题分析。' : `已交付 ${detail.task.report.changed_files?.length ?? 0} 个源码文件的修改；具体修改、代码差异和独立审查结果见下方。` };
  }
  if (state.label === '已跳过') {
    return { tone: 'idle' as const, title: '任务已跳过', desc: reason || '本次未交付，具体依据见分析与审计记录。' };
  }
  if (state.label === '失败') {
    return { tone: 'danger' as const, title: '任务失败', desc: reason || '执行或交付失败，查看审计中的失败步骤。' };
  }
  if (isReviewFailed(review)) {
    return {
      tone: 'danger' as const,
      title: '审查未完成：模型调用失败',
      desc: String(review?.contract_error || review?.summary || '审查调用失败，未取得有效结论。'),
    };
  }
  if (review?.verdict === 'MANUAL') {
    return {
      tone: 'warn' as const,
      title: '业务验证尚未完成',
      desc: reason || '当前审查没有充分的业务执行证据，任务仍按实际流程继续。',
    };
  }
  if (review?.verdict === 'FAIL' || state.label === '失败') {
    return { tone: 'danger' as const, title: '任务失败', desc: reason || '执行失败，查看审计中的失败步骤。' };
  }
  return { tone: 'info' as const, title: '任务执行中', desc: reason || '页面会随批次进度刷新。' };
}

function deliveryTone(delivery?: TaskDetailResponse['delivery']): 'ok' | 'warn' | 'danger' | 'idle' {
  if (!delivery) return 'idle';
  if (delivery.phase === 'failed') return 'danger';
  if (delivery.pushed) return 'ok';
  if (delivery.phase === 'prepared') return 'warn';
  return 'idle';
}

function deliveryLabel(delivery?: TaskDetailResponse['delivery']): string {
  if (!delivery) return '未交付';
  if (delivery.analysis_only) return '仅报告';
  if (delivery.phase === 'failed') return '交付失败';
  if (delivery.pushed) return '已交付';
  if (delivery.phase === 'prepared') return '待推送';
  return '未交付';
}
