import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { formatBatchTime, shortId } from '../lib/format';
import { Card, CardBody, CardHead, ErrorBox, JsonBlock, PageHeader, Skeleton, Tabs, useToast } from '../components/ui';

export function RunArtifactsPage() {
  const { runId = '' } = useParams();
  const [selected, setSelected] = useState('');
  const { push } = useToast();
  const run = useQuery({ queryKey: ['run', runId], queryFn: () => api.run(runId), enabled: Boolean(runId) });
  const artifacts = run.data?.artifacts ?? [];
  const path = selected || artifacts[0]?.path || '';
  const content = useQuery({
    queryKey: ['artifact-content', runId, '', path],
    queryFn: () => api.artifactContent(runId, path),
    enabled: Boolean(runId && path),
  });

  const download = async (target: string) => {
    try {
      const blob = await api.downloadArtifact(runId, target);
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

  if (run.isLoading && !run.data) return <Card><CardBody><Skeleton rows={8} /></CardBody></Card>;
  if (run.error || !run.data) return <Card><ErrorBox error={run.error ?? new Error('批次不存在')} /></Card>;

  return (
    <>
      <PageHeader
        eyebrow={`Run / ${shortId(runId, 8)}`}
        title={<>批次 <span className="mono">{shortId(runId, 8)}</span></>}
        meta={<>{run.data.task_count} 个任务<span className="dot">·</span>创建于 {formatBatchTime(run.data.started_at)}<span className="dot">·</span>批次级产物</>}
        actions={<Link className="btn" to={`/runs/${runId}`}>返回任务</Link>}
      />
      <Tabs
        active="artifacts"
        items={[
          { key: 'tasks', label: '任务', count: run.data.task_count, href: `/runs/${runId}` },
          { key: 'records', label: '记录', href: `/runs/${runId}/records` },
          { key: 'artifacts', label: '产物', count: artifacts.length, href: `/runs/${runId}/artifacts` },
        ]}
      />

      <div className="layout">
        <div>
          <Card>
            <CardHead title="批次产物" sub={`共 ${artifacts.length} 个`} />
            {artifacts.length === 0 ? (
              <div className="empty"><div className="empty-icon">·</div><h3>还没有产物</h3><p>批次结束后，报告与证据会出现在这里。</p></div>
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
            <div className="card-foot"><span>批次级产物 · 任务产物在任务审计中查看</span><span className="cell-muted">{artifacts.length} 个</span></div>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="预览" sub={path ? path.split('/').pop() : undefined} actions={<span className="tag blue">JSON</span>} />
            <CardBody>
              {!path ? (
                <p className="note">选择一个产物查看内容。</p>
              ) : content.isLoading ? (
                <Skeleton rows={5} />
              ) : content.error ? (
                <ErrorBox error={content.error} retry={() => void content.refetch()} />
              ) : (
                <JsonBlock value={content.data?.content} maxHeight={520} />
              )}
            </CardBody>
            {path ? (
              <div className="card-foot"><span>只读预览</span><button className="link" onClick={() => void download(path)}>下载 →</button></div>
            ) : null}
          </Card>
        </aside>
      </div>
    </>
  );
}
