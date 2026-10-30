import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Card, CardBody, CardHead, CopyButton, ErrorBox, JsonBlock, Skeleton, useToast } from '../components/ui';
import { TaskAuditTabs, TaskHeader } from '../components/TaskHeader';

export function TaskArtifactsPage() {
  const { runId = '', taskId = '' } = useParams();
  const [selected, setSelected] = useState('');
  const { push } = useToast();
  const detail = useQuery({
    queryKey: ['task', runId, taskId],
    queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
  });
  const artifacts = detail.data?.artifacts ?? [];
  const path = selected || artifacts[0]?.path || '';
  const content = useQuery({
    queryKey: ['artifact-content', runId, taskId, path],
    queryFn: () => api.artifactContent(runId, path, taskId),
    enabled: Boolean(runId && taskId && path),
  });

  const download = async (target: string) => {
    try {
      const blob = await api.downloadArtifact(runId, target, taskId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = target.split('/').pop() || 'artifact.json';
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      push('下载失败', error instanceof Error ? error.message : '请求失败', 'danger');
    }
  };

  if (detail.isLoading && !detail.data) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (detail.error || !detail.data) return <Card><ErrorBox error={detail.error ?? new Error('任务不存在')} /></Card>;

  const preview = content.data?.content;

  return (
    <>
      <TaskHeader detail={detail.data} active="audit" />
      <TaskAuditTabs active="artifacts" />

      <div className="layout">
        <div>
          <Card>
            <CardHead title="任务产物" sub={`${artifacts.length} 个 · 按 task_id 隔离`} />
            {artifacts.length === 0 ? (
              <div className="empty"><div className="empty-icon">·</div><h3>还没有任务产物</h3><p>任务执行后，报告、diff、review 会出现在这里。</p></div>
            ) : (
              <div className="card-body flush">
                <table className="table">
                  <thead><tr><th>产物</th><th>路径</th><th>类型</th><th /></tr></thead>
                  <tbody>
                    {artifacts.map((artifact) => (
                      <tr
                        key={`${artifact.path}-${artifact.task_id ?? ''}`}
                        style={{ cursor: 'pointer', background: path === artifact.path ? '#f8fafc' : undefined }}
                        onClick={() => setSelected(artifact.path)}
                      >
                        <td>
                          <div className="row-title">{artifact.name}</div>
                          <div className="row-sub">{artifact.description || '—'}</div>
                        </td>
                        <td className="cell-mono">{artifact.path}</td>
                        <td><span className="tag blue">{(artifact.media_type || 'application/json').split('/').pop()?.toUpperCase()}</span></td>
                        <td className="cell-right">
                          <button className="link" onClick={(event) => { event.stopPropagation(); setSelected(artifact.path); }}>预览</button>
                          <span className="muted"> · </span>
                          <button className="link" onClick={(event) => { event.stopPropagation(); void download(artifact.path); }}>下载</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="card-foot"><span>共 {artifacts.length} 个任务产物</span><span className="cell-muted">Agent 事件与 Prompt 快照在左侧子页</span></div>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead
              title="预览"
              sub={path ? path.split('/').pop() : undefined}
              actions={preview !== undefined ? <CopyButton text={typeof preview === 'string' ? preview : JSON.stringify(preview, null, 2)} label="复制" /> : undefined}
            />
            <CardBody>
              {!path ? (
                <p className="note">选择一个产物查看内容。</p>
              ) : content.isLoading ? (
                <Skeleton rows={5} />
              ) : content.error ? (
                <ErrorBox error={content.error} retry={() => void content.refetch()} />
              ) : (
                <JsonBlock value={preview} maxHeight={520} />
              )}
            </CardBody>
            {path ? <div className="card-foot"><span>只读预览</span><button className="link" onClick={() => void download(path)}>下载 →</button></div> : null}
          </Card>
        </aside>
      </div>
    </>
  );
}
