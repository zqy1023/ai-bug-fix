import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button,  Empty, Skeleton, Tag } from 'antd';
import {
  CheckCircleOutlined, CodeOutlined, DiffOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ApprovalItem, StepRecord } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime } from '../../lib/format';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';
import { VerificationChecklist } from '../../components/VerificationChecklist';
import { KeyValueList, Panel } from '../../components/ui';
import styles from './ReviewPane.module.css';

type Dict = Record<string, unknown>;
type FileEvidence = Dict & {
  path: string;
  status?: string;
  added?: number;
  removed?: number;
  problems?: string[];
};
function asObject(value: unknown): Dict {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Dict
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function statusLabel(value: unknown): string {
  switch (text(value).toLowerCase()) {
    case 'added': return '新增';
    case 'deleted': return '删除';
    case 'renamed': return '重命名';
    case 'modified': return '修改';
    case 'declared': return '方案声明';
    case 'unchanged':
    case 'same': return '未变化';
    case 'truncated': return '已截断';
    default: return text(value) || '修改';
  }
}

function stringField(source: Dict, ...keys: string[]): string {
  for (const key of keys) {
    const value = text(source[key]);
    if (value) return value;
  }
  return '';
}

function rawStringField(source: Dict, ...keys: string[]): string {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return '';
}

function shortText(value: string, maxChars = 200): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  return normalized.length > maxChars
    ? `${normalized.slice(0, maxChars).trimEnd()}…`
    : normalized;
}

function stepOf(steps: StepRecord[], name: string): StepRecord | undefined {
  return [...steps].reverse().find((step) => step.name === name);
}

/** 审阅面板：将原始异常、确定性文件差异和审批方案放在同一处核验。 */
export function ReviewPane({ item }: { item: ApprovalItem }) {
  const navigate = useNavigate();
  const [diffOpen, setDiffOpen] = useState(false);
  const [selectedDiffPath, setSelectedDiffPath] = useState('');

  const taskQuery = useQuery(workbenchQueries.task(item.run_id, item.task_id));
  const taskDetail = taskQuery.data;
  const task = taskDetail?.task;
  const steps = task?.steps ?? [];
  const checkStep = stepOf(steps, 'check_changes');
  const checkOutput = asObject(checkStep?.output);
  const gateFilesAvailable = Boolean(checkStep && Array.isArray(checkOutput.files));
  const gateFiles = (Array.isArray(checkOutput.files) ? checkOutput.files : [])
    .filter((file): file is FileEvidence => {
      const value = asObject(file);
      return Boolean(text(value.path)) && text(value.status).toLowerCase() !== 'truncated';
    })
    .map((file) => file as FileEvidence);

  // 有确定性门禁证据时，按实际状态显示；只有旧记录缺少门禁输出时才降级显示方案声明。
  const changedFiles: FileEvidence[] = gateFilesAvailable
    ? gateFiles.filter((file) => !['unchanged', 'same'].includes(text(file.status).toLowerCase()))
    : (item.changed_files ?? []).map((path) => ({ path, status: 'declared' }));
  const edits = item.change_plan?.edits ?? [];
  const editOf = (path: string) => edits.find((edit) => edit.path === path);
  const evidenceOf = (path: string) => gateFiles.find((file) => file.path === path);

  const diffArtifact = useMemo(() => {
    const stepArtifact = checkStep?.artifacts?.find(
      (artifact) => artifact.name === 'diff' || artifact.path.endsWith('/diff.json'),
    );
    return stepArtifact ?? taskDetail?.artifacts.find(
      (artifact) => artifact.name === 'diff' || artifact.path.endsWith('/diff.json'),
    );
  }, [checkStep?.artifacts, taskDetail?.artifacts]);

  const diffQuery = useQuery({
    queryKey: [...queryKeys.artifactContent(item.run_id, diffArtifact?.path ?? '', item.task_id), item.delivery?.candidate_id, checkStep?.finished_at, checkStep?.attempt],
    queryFn: () => api.artifactContent(item.run_id, diffArtifact!.path, item.task_id),
    enabled: Boolean(diffArtifact),
    staleTime: 60_000,
  });

  const diffPayload = diffQuery.data?.content;

  const problem = asObject(task?.problem);
  // identify_carrier preserves the error field from the normalized ES record as the original log text.
  const originalError = rawStringField(problem, 'error', 'raw_error');
  const serviceName = stringField(problem, 'service_name');
  const totalAdded = changedFiles.reduce((sum, file) => sum + (Number(file.added) || 0), 0);
  const totalRemoved = changedFiles.reduce((sum, file) => sum + (Number(file.removed) || 0), 0);
  const fileProblems = changedFiles.flatMap((file) => {
    const evidence = evidenceOf(file.path);
    return Array.isArray(evidence?.problems)
      ? evidence.problems.map((value) => `${file.path}：${String(value)}`)
      : [];
  });

  return (
    <article className={styles.pane}>
      <header className={styles.head}>
        <div>
          <span className={styles.kicker}>交付审批 · {item.approval_id}</span>
          <h2 className={styles.title}>{item.carrier || '未识别运输商'}</h2>
          <p className={styles.summaryText}>{shortText(item.summary || '结合原始异常与实际代码差异进行核对。', 180)}</p>
          {(item.summary?.length ?? 0) > 180 && (
            <details className={styles.textDetails}>
              <summary>完整摘要</summary>
              <p>{item.summary}</p>
            </details>
          )}
        </div>
        <Tag color={item.scope === 'carrier' ? 'blue' : 'orange'}>
          {item.scope === 'carrier' ? '运输商范围' : '项目 / 共享范围'}
        </Tag>
      </header>

      {task?.approval_pending && text(asObject(taskDetail?.run_error).status).toUpperCase() === 'FAILED' && (
        <section className={styles.recovery} role="alert">
          <strong>上次审批恢复失败</strong>
          <p>{shortText(stringField(asObject(taskDetail?.run_error), 'terminal_reason', 'message') || '后台恢复未完成，待审批记录仍保留。', 220)}</p>
          <span>修复运行环境后，可重新提交决策；恢复失败不会被当成已重跑。</span>
        </section>
      )}

      <details className={styles.candidate}><summary>候选与审批身份 <code>{item.delivery?.commit || '尚未生成提交'}</code> · {item.delivery?.branch || '未记录分支'}</summary>
      <Panel
        title="候选提交"
        extra={<span>candidate {item.delivery?.candidate_id ? item.delivery.candidate_id.slice(0, 8) : '—'}</span>}
      >
        <KeyValueList items={[
          { label: '提交', value: item.delivery?.commit || '未记录' },
          { label: '目标分支', value: item.delivery?.branch || '未记录' },
          { label: '候选 ID', value: item.delivery?.candidate_id || '未记录' },
          { label: '交付状态', value: item.delivery?.phase === 'prepared' ? '本地候选已准备，等待批准后正式交付' : '请核对候选与审批状态', text: true },
        ]} />
        <details className={styles.moreIdentity}><summary>执行、任务与审批时间</summary>
        <KeyValueList items={[
          {
            label: '执行',
            value: (
              <button
                type="button"
                className="link-button"
                onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}`)}
              >
                run:{item.run_id}
              </button>
            ),
          },
          {
            label: '任务',
            value: (
              <button
                type="button"
                className="link-button"
                onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`)}
              >
                {item.task_id}
              </button>
            ),
          },
          { label: '服务', value: serviceName || '未记录', text: true },
          { label: '申请时间', value: formatTime(item.created_at) },
          { label: '审批到期', value: item.expires_at ? formatTime(item.expires_at) : '未设置' },
        ]} />
        </details>
      </Panel>

      </details>
      <div className={styles.approvalChecks}><Panel title="修复后代码审查" flush>
        {taskQuery.isLoading ? <div className={styles.padCheck}><Skeleton active paragraph={{ rows: 3 }} /></div> : <VerificationChecklist layout="ribbon" steps={steps} onOpenStep={(name) => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}?view=execution&step=${encodeURIComponent(name)}`)} />}
      </Panel>
      </div>
      <Panel title="代码差异" extra={<Button size="small" icon={<DiffOutlined />} disabled={!diffArtifact} onClick={() => setDiffOpen(true)}>展开对照</Button>}>
        <div className={styles.inlineDiff}>
          {diffQuery.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : diffQuery.isError ? <Empty description="读取代码差异失败" /> : diffPayload ? <CodeDiffViewer payload={diffPayload} selectedPath={selectedDiffPath} onSelectedPathChange={setSelectedDiffPath} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未提供代码差异产物" />}
        </div>
      </Panel>
      <Panel
        title={<span className={styles.checkMain}><WarningOutlined /> 原始 Error</span>}
        extra="原文保留，不用根因摘要替代"
      >
        {taskQuery.isLoading ? (
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        ) : originalError ? (
          <details className={styles.origFold}>
            <summary>
              <span>展开原始日志</span>
              <code>{shortText(originalError.split(/\r?\n/)[0] || originalError, 160)}</code>
            </summary>
            <pre className={styles.origText}>{originalError}</pre>
          </details>
        ) : (
          <p className={styles.inlineEmpty}>
            未记录原始 error 字段。
            <Button
              type="link"
              size="small"
              onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`)}
            >
              查看任务详情
            </Button>
          </p>
        )}
        {taskQuery.isError && (
          <div className={styles.inlineError}>任务详情读取失败，无法展示原始日志。</div>
        )}
      </Panel>

      <Panel title="方案说明">
        {item.change_plan?.rationale ? (
          <>
            <p className={styles.rationale}>{shortText(item.change_plan.rationale, 220)}</p>
            {item.change_plan.rationale.length > 220 && (
              <details className={styles.textDetails}>
                <summary>展开完整方案说明</summary>
                <p>{item.change_plan.rationale}</p>
              </details>
            )}
          </>
        ) : (
          <p className={styles.rationale}>未提供额外方案说明。</p>
        )}
      </Panel>
      {edits.length > 0 && (
        <Panel flush title="方案编辑清单">
          <ul className={styles.editList}>
            {edits.map((edit) => (
              <li key={`${edit.path}-${edit.action}`} className={styles.editRow}>
                <code>{edit.path}</code>
                <Tag>{edit.action}</Tag>
                <span>{edit.note || '—'}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        flush
        title={<span className={styles.checkMain}><CodeOutlined /> 验证覆盖</span>}
        extra={(
          <Button
            size="small"
            icon={<DiffOutlined />}
            disabled={!diffArtifact}
            onClick={() => {
              setSelectedDiffPath('');
              setDiffOpen(true);
            }}
          >
            查看完整 Diff
          </Button>
        )}
      >
        {taskQuery.isLoading ? (
          <div className={styles.padCheck}>
            <Skeleton active title={false} paragraph={{ rows: 4 }} />
          </div>
        ) : changedFiles.length ? (
          <>
            <div className={styles.padCheck}>
              <div className={styles.summaryLine}>
                <span>{gateFilesAvailable ? '确定性门禁文件证据' : '方案声明文件（未取得确定性门禁证据）'}</span>
                <strong><span className={styles.added}>+{totalAdded}</span> / <span className={styles.removed}>−{totalRemoved}</span> 行</strong>
              </div>
            </div>
            <ul className={styles.checkList}>
              {changedFiles.map((file) => {
                const evidence = evidenceOf(file.path);
                const fileProblem = Array.isArray(evidence?.problems)
                  ? evidence.problems.map((value) => String(value)).filter(Boolean)
                  : [];
                const warned = !gateFilesAvailable || text(file.status) === 'declared' || fileProblem.length > 0;
                return (
                  <li key={file.path} className={`${styles.check}${warned ? ` ${styles.checkWarn}` : ''}`}>
                    {warned ? <WarningOutlined className={styles.checkIcon} /> : <CheckCircleOutlined className={styles.checkIcon} />}
                    <div className={styles.checkBody}>
                      <div className={styles.checkMain}>
                        <code className={styles.checkCode} title={file.path}>{file.path}</code>
                        <Tag color={text(file.status) === 'declared' ? 'gold' : 'blue'}>{statusLabel(file.status)}</Tag>
                        <span className={styles.checkLines}>
                          <b className={styles.added}>+{Number(file.added) || 0}</b>
                          <b className={styles.removed}>−{Number(file.removed) || 0}</b>
                        </span>
                        <Button
                          type="link"
                          size="small"
                          disabled={!diffArtifact}
                          onClick={() => {
                            setSelectedDiffPath(file.path);
                            setDiffOpen(true);
                          }}
                        >
                          查看修改
                        </Button>
                      </div>
                      {fileProblem.length > 0 && <small className={styles.problems}>{fileProblem.join('；')}</small>}
                      {editOf(file.path)?.note && <small className={styles.problems}>{editOf(file.path)!.note}</small>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <p className={styles.padNote}>
            {gateFilesAvailable
              ? '确定性门禁输出中没有实际变化文件；请勿把 Agent 的修改声明当成真实差异。'
              : '未返回文件清单。请先检查任务详情中的 check_changes 步骤记录。'}
          </p>
        )}
        {!diffArtifact && !taskQuery.isLoading && (
          <p className={styles.padTail}>任务没有提供 diff 产物，当前无法展示逐行修改内容。</p>
        )}
      </Panel>

      <Panel title="审查发现">
        {fileProblems.length > 0 ? (
          <ul className={styles.checkList}>
            {fileProblems.map((line, index) => (
              <li key={index} className={`${styles.check} ${styles.checkWarn}`}>
                <WarningOutlined className={styles.checkIcon} />
                <span className={styles.checkText}>{line}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.emptyNote}>没有记录审查发现；请结合原始 Error 与门禁文件证据核对。</p>
        )}
      </Panel>

      <p className={styles.caution}>
        批准后系统核验并正式交付此候选提交，不会自动合并；驳回后当前候选失效，并从指定阶段回修。
      </p>

      <WorkbenchDrawer
        title="文件修改详情 · 源码左右对照"
        placement="right"
        width="min(1440px, 97vw)"
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
        styles={{ body: { padding: 10, overflow: 'hidden' } }}
      >
        {diffQuery.isLoading ? (
          <Skeleton active paragraph={{ rows: 10 }} />
        ) : diffQuery.isError ? (
          <Empty description="读取 diff 产物失败，请检查 check_changes 的步骤记录。" />
        ) : diffPayload ? (
          <CodeDiffViewer
            payload={diffPayload}
            selectedPath={selectedDiffPath}
            onSelectedPathChange={setSelectedDiffPath}
          />
        ) : (
          <Empty description="没有可显示的代码差异。" />
        )}
      </WorkbenchDrawer>
    </article>
  );
}
