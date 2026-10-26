import { StepAssessment } from './StepAssessment';
import { StepOutcome } from './StepOutcome';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Skeleton, Tabs } from 'antd';
import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { JsonDocument } from '../../components/JsonDocument';
import { FileTextOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration } from '../../lib/format';
import type { ArtifactRef, StepDetailResponse, StepRecord } from '../../lib/types';
import { JsonViewer } from '../../components/JsonViewer';
import { EmptyState } from '../../components/EmptyState';
import { StatusBadge } from '../../components/StatusBadge';
import { Panel, Segmented } from '../../components/ui';
import { AgentTrace } from '../agent/AgentTrace';
import { StepActionBar } from './StepActionBar';
import { FAILED_RESULTS } from './stages';
import styles from './StepDetail.module.css';

function compactText(value: string, maxChars: number): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  return normalized.length > maxChars
    ? `${normalized.slice(0, maxChars).trimEnd()}…`
    : normalized;
}

function ArtifactsPanel({
  runId, taskId, artifacts,
}: { runId: string; taskId: string; artifacts: ArtifactRef[] }) {
  const [openPath, setOpenPath] = useState('');
  const content = useQuery({
    queryKey: queryKeys.artifactContent(runId, openPath, taskId),
    queryFn: () => api.artifactContent(runId, openPath, taskId),
    enabled: Boolean(openPath),
  });

  if (!artifacts.length) {
    return <EmptyState title="该步骤没有产物引用" description="大体积输出会落在 artifacts 目录。" compact />;
  }

  return (
    <div className={styles.artifacts}>
      <ul className={styles.artifactList}>
        {artifacts.map((artifact) => (
          <li key={`${artifact.path}-${artifact.name}`}>
            <button
              type="button"
              className={`${styles.artifactRow}${openPath === artifact.path ? ` ${styles.artifactRowActive}` : ''}`}
              onClick={() => setOpenPath(openPath === artifact.path ? '' : artifact.path)}
            >
              <FileTextOutlined />
              <span className={styles.artifactCopy}>
                <strong>{artifact.name}</strong>
                <code>{artifact.path}</code>
              </span>
              <span className={styles.artifactAction}>{openPath === artifact.path ? '收起' : '查看'}</span>
            </button>
          </li>
        ))}
      </ul>
      {openPath && (
        <div className={styles.artifactContent}>
          {content.isLoading && <Skeleton active paragraph={{ rows: 4 }} />}
          {content.isError && <EmptyState title="产物读取失败" compact />}
          {content.data && <JsonViewer value={content.data.content} defaultExpandDepth={1} />}
        </div>
      )}
    </div>
  );
}

const VERDICT_LABELS = { passed: '业务通过', failed: '业务未通过', inconclusive: '尚无定论', not_applicable: '不涉及业务验收' } as const;
const VERDICT_CLASS = { passed: 'verdictPassed', failed: 'verdictFailed', inconclusive: 'verdictInconclusive', not_applicable: 'verdictNa' } as const;

export function StepDetail({
  runId, taskId, step, detail, runActive, approvalPending = false,
}: {
  runId: string; taskId: string; step: StepRecord;
  detail?: StepDetailResponse; runActive: boolean; approvalPending?: boolean;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  // 详情请求完成后使用该步骤的最新记录，避免继续展示列表中的旧投影。
  step = detail?.step.name === step.name ? detail.step : step;
  const tab = searchParams.get('trace');
  const stepFailed = (step.execution?.status ?? step.status) === 'failed'
    || step.assessment?.outcome === 'failed'
    || (step.result ? FAILED_RESULTS.has(String(step.result).toUpperCase()) : false);

  const items = [
    { key: 'conclusion', label: '结论与证据', children: <StepOutcome step={step} /> },
    { key: 'input', label: '原始输入', children: <JsonDocument value={step.input ?? {}} filename={`${step.name}-input.json`} /> },
    {
      key: 'output',
      label: '原始输出',
      children: step.status === 'running' || step.status === 'pending'
        ? <EmptyState title="输出尚未落盘" description="步骤仍在执行或尚未开始；完成后输出会写入步骤记录。" compact />
        : <JsonDocument value={step.output ?? {}} filename={`${step.name}-output.json`} />,
    },
    {
      key: 'trace',
      label: 'Agent 追踪',
      children: (
        <AgentTrace
          runId={runId}
          taskId={taskId}
          stepName={step.name}
          attempt={step.attempt}
        />
      ),
    },
    {
      key: 'artifacts',
      label: `产物${step.artifacts?.length ? ` (${step.artifacts.length})` : ''}`,
      children: <ArtifactsPanel runId={runId} taskId={taskId} artifacts={step.artifacts ?? []} />,
    },
    {
      key: 'error',
      label: '结论 / 错误',
      children: (
        <div className={styles.errorPanel}>
          <div className={styles.resultBlock}>
            <h4 className={styles.subhead}>本步骤</h4>
            <div className={styles.factRow}><span>业务结论</span><strong>{step.result ?? '—'}</strong></div>
            {step.error ? (
              <div className={styles.errorBox}>
                <strong>{step.error.code ?? 'STEP_EXECUTION_FAILED'}</strong>
                <p>{compactText(step.error.message ?? '没有错误详情', 360)}</p>
                {(step.error.message?.length ?? 0) > 360 && (
                  <details className={styles.longText}>
                    <summary>查看完整错误（{step.error.message?.length ?? 0} 字符）</summary>
                    <pre>{step.error.message}</pre>
                  </details>
                )}
                <span>可重试：{step.error.retryable ? '是' : '否'}</span>
              </div>
            ) : stepFailed ? null : <EmptyState title="该步骤没有错误" description="失败步骤才会记录错误对象。" compact />}
          </div>
        </div>
      ),
    },
  ];

  const currentTab = tab ?? 'conclusion';
  const drawerKeys = new Set(['input', 'output', 'trace', 'artifacts', 'context']);
  const drawerItems = [...items.filter(item => drawerKeys.has(item.key)), { key: 'context', label: '上下文', children: <JsonDocument value={detail?.context ?? {}} filename={`${step.name}-context.json`} /> }];
  const changeTab = (key: string) => {
    // 只切换详情 Tab，必须保留当前选中步骤；否则会回落到默认步骤，
    // 表现为“点任何步骤的输出都跳到另一个步骤”。
    const next = new URLSearchParams(searchParams);
    next.set('trace', key);
    next.set('step', step.name);
    setSearchParams(next, { replace: true });
  };

  return (
    <section className={styles.detail}>
      <header className={styles.head}>
        <span className={styles.kicker}>步骤 / {step.name}</span>
        <div className={styles.titleRow}>
          <h3>{step.title || step.name}</h3>
          {step.assessment ? (
            <>
              <span className={styles.execNote}>{({ succeeded: '执行完成', failed: '执行失败', waiting: '等待输入', cancelled: '已取消' })[step.execution?.status || 'succeeded']}</span>
              <span className={`${styles.verdictChip} ${styles[VERDICT_CLASS[step.assessment.outcome] ?? 'verdictNa']}`}>
                {VERDICT_LABELS[step.assessment.outcome] ?? step.assessment.outcome}
              </span>
            </>
          ) : <StatusBadge kind="step" value={step.status} stepName={step.name} />}
          {step.attempt ? <span className={styles.chip}>节点第 {step.attempt} 次执行</span> : null}
          <span className={styles.chip}>
            {step.status === 'running' ? '执行中…' : formatDuration(step.duration_ms)}
          </span>
          {detail?.model_call_ids.length ? (
            <span className={styles.chip}>{detail.model_call_ids.length} 次模型调用</span>
          ) : null}
        </div>
        {step.summary && step.name !== 'review_agent' && (
          <div className={styles.summaryBlock}>
            {step.summary.length > 180 ? <details className={styles.summaryExpand}><summary>{compactText(step.summary, 180)} <span>展开全文</span></summary><p>{step.summary}</p></details> : <p className={styles.summary}>{step.summary}</p>}
          </div>
        )}
      </header>

      <StepAssessment step={step} />

      <Panel
        title={<span className={styles.evidenceTitle}><span>证据与记录</span><code>{step.name}</code></span>}
        extra={(
          <Segmented
            options={items.map(({ key, label }) => ({ value: key, label }))}
            value={currentTab}
            onChange={changeTab}
            ariaLabel="步骤证据分区"
          />
        )}
        flush
      >
        <div className={styles.body}>
          {items.find((item) => item.key === (drawerKeys.has(currentTab) ? 'conclusion' : currentTab))?.children}
        </div>
      </Panel>

      <WorkbenchDrawer title={<span>{step.title || step.name} · 执行证据</span>} open={drawerKeys.has(currentTab)} onClose={() => changeTab('conclusion')} width="min(860px, 100vw)" styles={{ body: { padding: 0 } }}>
        <div className={styles.drawerContext}><span>批次 <code>{runId}</code></span><span>任务 <code>{taskId}</code></span><span>节点 <code>{step.name}</code> · 第 {step.attempt || 1} 次</span></div>
        <Tabs className={styles.drawerTabs} activeKey={currentTab} onChange={changeTab} items={drawerItems} />
      </WorkbenchDrawer>
      <StepActionBar
        availability={detail?.actions?.rerun}
        continuation={detail?.actions?.continue_after}
        runId={runId}
        taskId={taskId}
        stepName={step.name}
        stepStatus={stepFailed ? 'failed' : step.status}
        runActive={runActive}
        approvalPending={approvalPending}
      />
    </section>
  );
}
