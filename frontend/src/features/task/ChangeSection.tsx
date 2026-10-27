import { useMemo, useState } from 'react';
import { Button, Input, Skeleton } from 'antd';
import { DiffOutlined, SearchOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { TaskDetailResponse } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { EmptyState } from '../../components/EmptyState';
import type { ChangeFile, TaskView } from './taskVerdict';
import { Fold, Section as TaskSection, SectionEmpty, ToneTag } from '../../components/section/Section';
import styles from './TaskSections.module.css';

type Filter = 'all' | 'modified' | 'added' | 'deleted';

const STATUS_TEXT: Record<string, string> = {
  declared: '待核验', added: '新增', new: '新增', created: '新增',
  deleted: '删除', removed: '删除', renamed: '重命名', moved: '重命名',
  modified: '修改',
};

function statusText(value: string): string {
  return STATUS_TEXT[value.toLowerCase()] || '修改';
}

/** 状态归并：renamed/declared 等落在“修改”档，筛选才有确定语义。 */
function statusBucket(value: string): Exclude<Filter, 'all'> {
  const status = value.toLowerCase();
  if (['added', 'new', 'created'].includes(status)) return 'added';
  if (['deleted', 'removed'].includes(status)) return 'deleted';
  return 'modified';
}

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'modified', label: '修改' },
  { key: 'added', label: '新增' },
  { key: 'deleted', label: '删除' },
];

/** 代码改动分区：实际变更 vs 声明待核验要一眼分清，文件全部列出而不是只露 3 条。 */
export function ChangeSection({ detail, view, onOpenStep }: {
  detail: TaskDetailResponse;
  view: TaskView;
  onOpenStep: (name: string) => void;
}) {
  const { changes, delivery, repairStep } = view;
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [diffOpen, setDiffOpen] = useState(false);
  const [selectedPath, setSelectedPath] = useState('');

  const steps = detail.task.steps ?? [];
  const diffArtifact = useMemo(() => {
    const fromSteps = [...steps].reverse().flatMap((step) => step.artifacts || []);
    return fromSteps.concat(detail.artifacts || [])
      .find((artifact) => artifact.name === 'diff' || /[/\\]diff\.json$/.test(artifact.path));
  }, [steps, detail.artifacts]);

  const diffQuery = useQuery({
    queryKey: [...queryKeys.artifactContent(detail.run_id, diffArtifact?.path || '', detail.task.task_id), 'diff-drawer', diffOpen],
    queryFn: () => api.artifactContent(detail.run_id, diffArtifact!.path, detail.task.task_id),
    enabled: diffOpen && Boolean(diffArtifact),
    staleTime: 60_000,
  });

  const repairSummary = repairStep ? (getRepairSummary(repairStep) || '') : '';
  const visible = changes.files.filter((file) => {
    if (filter !== 'all' && statusBucket(file.status) !== filter) return false;
    if (!search.trim()) return true;
    return file.path.toLowerCase().includes(search.trim().toLowerCase());
  });

  const openDiff = (path: string) => {
    setSelectedPath(path);
    setDiffOpen(true);
  };

  const countLabel = changes.evidenceKnown
    ? `${changes.count} 个真实变更文件`
    : `${changes.count} 个声明文件（待核验）`;

  return (
    <TaskSection
      title="代码改动"
      subtitle="区分工作区改动与真正提交到分支的源码"
      extra={(
        <ToneTag tone={delivery.deliveredCode ? 'ok' : changes.evidenceKnown && changes.count > 0 ? 'warn' : changes.evidenceKnown ? 'muted' : changes.count > 0 ? 'warn' : 'muted'}>
          {delivery.deliveredCode ? '源码已提交'
            : delivery.analysisOnly ? '仅交付报告'
              : changes.count > 0 && changes.evidenceKnown ? '实际改动未交付'
                : changes.count > 0 ? '改动声明待核验'
                  : changes.evidenceKnown ? '无实际差异'
                    : '暂无改动记录'}
        </ToneTag>
      )}
    >
      {delivery.analysisOnly && (
        <div className={styles.pad}>
          <div className={styles.banner} role="status">本次仅提交分析报告；源码改动未随该分支交付。</div>
        </div>
      )}

      {repairSummary && (
        <div className={styles.pad}>
          <Fold summary="修复说明">
            <p className={styles.paragraph}>{repairSummary}</p>
          </Fold>
        </div>
      )}

      {changes.files.length > 0 ? (
        <>
          <div className={styles.countRow}>
            <strong>{countLabel}</strong>
            {changes.evidenceKnown && <span className={styles.addNum}>+{changes.added}</span>}
            {changes.evidenceKnown && <span className={styles.delNum}>−{changes.removed}</span>}
          </div>
          <div className={styles.filterRow}>
            <div className={styles.filters} role="group" aria-label="按状态过滤">
              {FILTERS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={filter === item.key}
                  className={filter === item.key ? styles.filterOn : undefined}
                  onClick={() => setFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <Input
              size="small"
              allowClear
              className={styles.fileSearch}
              prefix={<SearchOutlined />}
              placeholder="搜索文件路径"
              aria-label="搜索变更文件"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <ul className={styles.fileList}>
            {visible.map((file) => <FileRow key={file.path} file={file} canOpen={Boolean(diffArtifact)} onOpen={() => openDiff(file.path)} />)}
          </ul>
          {visible.length === 0 && (
            <SectionEmpty title="没有匹配的文件" body={search ? '当前筛选条件下没有文件命中该路径。' : '当前状态档位下没有文件。'} />
          )}
          <div className={styles.footRow}>
            {diffArtifact && (
              <Button size="small" icon={<DiffOutlined />} onClick={() => openDiff('')}>查看代码差异</Button>
            )}
            {changes.step && <Button size="small" type="link" onClick={() => onOpenStep(changes.step!.name)}>变更门禁详情</Button>}
          </div>
        </>
      ) : (
        <SectionEmpty
          title={changes.checkState === 'not_returned' ? '变更门禁证据未回传'
            : changes.listMissing ? '变更核验已执行，但没有回传文件清单'
              : changes.evidenceKnown ? '没有检测到真实源码差异'
                : changes.claimedFiles.length ? '修改声明尚未核验' : '没有可核验的源码差异'}
          body={changes.checkState === 'not_returned'
            ? '审查内部的变更核验已执行，但结果没有回传到本任务，不能据此判断是否存在真实差异。'
            : changes.listMissing
              ? '核验结论已经回传，文件级差异清单没有随审查结论返回；请用“查看代码差异”核对逐行差异。'
              : changes.evidenceKnown ? '确定性门禁已比对基线；未将模型声明当作实际修改。'
                : changes.claimedFiles.length ? '修复 Agent 声明了候选文件，但还没有确定性差异证据。'
                  : '任务可能只做根因分析，或在修改前已终止。'}
        />
      )}

      <WorkbenchDrawer
        title={`代码差异 · ${diffArtifact?.path || 'diff.json'}`}
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
        width="min(1400px, 96vw)"
        styles={{ body: { padding: 10, overflow: 'hidden' } }}
      >
        {diffQuery.isLoading ? <Skeleton active paragraph={{ rows: 8 }} />
          : diffQuery.isError ? <EmptyState title="差异产物读取失败" description="请到变更门禁步骤核对错误。" />
            : diffQuery.data?.content ? (
              <CodeDiffViewer payload={diffQuery.data.content} selectedPath={selectedPath} onSelectedPathChange={setSelectedPath} />
            ) : (
              <EmptyState
                title="没有可展示的差异文本"
                description={changes.checkState === 'available'
                  ? '变更门禁已执行，但没有回传差异文本产物。'
                  : changes.checkState === 'not_returned'
                    ? '审查内部证据未回传，本任务没有可展示的差异文本。'
                    : '变更门禁未执行，本任务没有差异产物。'}
              />
            )}
      </WorkbenchDrawer>
    </TaskSection>
  );
}

function getRepairSummary(step: { output?: Record<string, unknown>; summary?: string }): string {
  const output = step.output ?? {};
  const value = output.summary ?? output.analysis;
  if (typeof value === 'string' && value.trim()) return value.trim();
  return typeof step.summary === 'string' ? step.summary.trim() : '';
}

function FileRow({ file, canOpen, onOpen }: { file: ChangeFile; canOpen: boolean; onOpen: () => void }) {
  const bucket = statusBucket(file.status);
  return (
    <li className={styles.fileRow}>
      <span className={`${styles.fileMark} ${styles[`mark${bucket[0].toUpperCase()}${bucket.slice(1)}`]}`} aria-hidden="true" />
      <span className={styles.fileStatus}>{statusText(file.status)}</span>
      <button type="button" className={styles.filePath} title={file.path} disabled={!canOpen} onClick={onOpen}>
        <code>{file.path}</code>
      </button>
      <span className={styles.fileLines}>
        {file.added > 0 && <b className={styles.addNum}>+{file.added}</b>}
        {file.removed > 0 && <b className={styles.delNum}>−{file.removed}</b>}
      </span>
    </li>
  );
}
