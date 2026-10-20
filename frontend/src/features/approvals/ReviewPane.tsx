import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Drawer, Empty, Skeleton, Tag } from 'antd';
import {
  CodeOutlined, DiffOutlined, FileTextOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ApprovalItem, StepRecord } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime } from '../../lib/format';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';

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

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="review-fact">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

/** 审阅面板：将原始异常、确定性文件差异和审批方案放在同一处核验。 */
export function ReviewPane({ item }: { item: ApprovalItem }) {
  const navigate = useNavigate();
  const [diffOpen, setDiffOpen] = useState(false);
  const [selectedDiffPath, setSelectedDiffPath] = useState('');

  const taskQuery = useQuery({
    queryKey: queryKeys.task(item.run_id, item.task_id),
    queryFn: () => api.task(item.run_id, item.task_id),
    staleTime: 30_000,
  });
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
    queryKey: queryKeys.artifactContent(item.run_id, diffArtifact?.path ?? '', item.task_id),
    queryFn: () => api.artifactContent(item.run_id, diffArtifact!.path, item.task_id),
    enabled: diffOpen && Boolean(diffArtifact),
    staleTime: 60_000,
  });

  const diffPayload = diffQuery.data?.content;

  const problem = asObject(task?.problem);
  // identify_carrier preserves the error field from the normalized ES record as the original log text.
  const originalError = rawStringField(problem, 'error', 'raw_error');
  const serviceName = stringField(problem, 'service_name');
  const totalAdded = changedFiles.reduce((sum, file) => sum + (Number(file.added) || 0), 0);
  const totalRemoved = changedFiles.reduce((sum, file) => sum + (Number(file.removed) || 0), 0);

  return (
    <article className="review-pane">
      <header className="review-head">
        <div>
          <span className="review-kicker">交付审批 · {item.approval_id}</span>
          <h2>{item.carrier || '未识别运输商'}</h2>
          <p>{shortText(item.summary || '结合原始异常与实际代码差异进行核对。', 180)}</p>
          {(item.summary?.length ?? 0) > 180 && (
            <details className="review-text-details">
              <summary>完整摘要</summary>
              <p>{item.summary}</p>
            </details>
          )}
        </div>
        <Tag color={item.scope === 'carrier' ? 'blue' : 'orange'}>
          {item.scope === 'carrier' ? '运输商范围' : '项目 / 共享范围'}
        </Tag>
      </header>

      <div className="review-facts">
        <Fact label="执行">
          <button
            type="button"
            className="link-button"
            onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}`)}
          >
            run:{item.run_id}
          </button>
        </Fact>
        <Fact label="任务">
          <button
            type="button"
            className="link-button"
            onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`)}
          >
            {item.task_id}
          </button>
        </Fact>
        <Fact label="服务">{serviceName || '未记录'}</Fact>
        <Fact label="申请时间">{formatTime(item.created_at)}</Fact>
        <Fact label="审批到期">{item.expires_at ? formatTime(item.expires_at) : '未设置'}</Fact>
      </div>

      <section className="review-section review-original-error">
        <header className="review-subhead">
          <h3><WarningOutlined /> 原始 Error</h3>
          <span>原文保留，不用根因摘要替代</span>
        </header>
        {taskQuery.isLoading ? (
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        ) : originalError ? (
          <details className="review-original-error-details">
            <summary>
              <span>展开原始日志</span>
              <code>{shortText(originalError.split(/\r?\n/)[0] || originalError, 160)}</code>
            </summary>
            <pre className="review-original-error-text">{originalError}</pre>
          </details>
        ) : (
          <div className="review-inline-empty">
            未记录原始 error 字段。
            <Button
              type="link"
              size="small"
              onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`)}
            >
              查看任务详情
            </Button>
          </div>
        )}
        {taskQuery.isError && (
          <div className="review-inline-error">任务详情读取失败，无法展示原始日志。</div>
        )}
      </section>

      <section className="review-section">
        <h3>方案说明</h3>
        {item.change_plan?.rationale ? (
          <>
            <p className="review-rationale">{shortText(item.change_plan.rationale, 220)}</p>
            {item.change_plan.rationale.length > 220 && (
              <details className="review-text-details">
                <summary>展开完整方案说明</summary>
                <p>{item.change_plan.rationale}</p>
              </details>
            )}
          </>
        ) : (
          <p className="review-rationale">未提供额外方案说明。</p>
        )}
        {edits.length > 0 && (
          <ul className="review-edit-list">
            {edits.map((edit) => (
              <li key={`${edit.path}-${edit.action}`}>
                <code>{edit.path}</code>
                <Tag>{edit.action}</Tag>
                <span>{edit.note || '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="review-section">
        <header className="review-subhead">
          <h3><CodeOutlined /> 文件修改详情 <span className="review-count">{changedFiles.length}</span></h3>
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
        </header>
        {taskQuery.isLoading ? (
          <Skeleton active title={false} paragraph={{ rows: 4 }} />
        ) : changedFiles.length ? (
          <>
            <div className="review-change-summary">
              <span>{gateFilesAvailable ? '确定性门禁文件证据' : '方案声明文件（未取得确定性门禁证据）'}</span>
              <strong><span className="review-added">+{totalAdded}</span> / <span className="review-removed">−{totalRemoved}</span> 行</strong>
            </div>
            <ul className="review-file-list review-file-detail-list">
              {changedFiles.map((file) => {
                const edit = editOf(file.path);
                const evidence = evidenceOf(file.path);
                const fileProblems = Array.isArray(evidence?.problems)
                  ? evidence.problems.map((value) => String(value)).filter(Boolean)
                  : [];
                return (
                  <li key={file.path} className="review-file-detail-row">
                    <span className="review-file-icon"><FileTextOutlined /></span>
                    <div className="review-file-detail-copy">
                      <code title={file.path}>{file.path}</code>
                      {fileProblems.length > 0 && (
                        <small className="review-file-problems">{fileProblems.join('；')}</small>
                      )}
                      {edit?.note && <small>{edit.note}</small>}
                    </div>
                    <Tag color={text(file.status) === 'declared' ? 'gold' : 'blue'}>{statusLabel(file.status)}</Tag>
                    <span className="review-file-lines">
                      <b className="review-added">+{Number(file.added) || 0}</b>
                      <b className="review-removed">−{Number(file.removed) || 0}</b>
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
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <p className="review-empty">
            {gateFilesAvailable
              ? '确定性门禁输出中没有实际变化文件；请勿把 Agent 的修改声明当成真实差异。'
              : '未返回文件清单。请先检查任务详情中的 check_changes 步骤记录。'}
          </p>
        )}
        {!diffArtifact && (
          <p className="review-inline-empty">任务没有提供 diff 产物，当前无法展示逐行修改内容。</p>
        )}
      </section>

      <p className="review-caution">
        批准只保留交付分支供人工合并，不会自动合入目标分支；驳回会删除该分支并回到修复链路。
      </p>

      <Drawer
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
      </Drawer>
    </article>
  );
}
