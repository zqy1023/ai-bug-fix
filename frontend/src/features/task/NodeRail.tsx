import { Fragment, useMemo, useState } from 'react';
import { DownOutlined, RightOutlined } from '@ant-design/icons';
import type { StepRecord, StepStatus, TaskDetailResponse } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';
import {
  AGENT_STEPS, FAILED_RESULTS, TASK_STAGES, primaryStep, stageForStep, stageProgress, stageStatus, stepRecord,
} from './stages';

type StageRow = {
  stage: (typeof TASK_STAGES)[number];
  status: StepStatus;
  progress: { done: number; total: number };
  records: StepRecord[];
  index: number;
  order: number;
  reason: string;
  late: boolean;
};

const ACTIVE_TASK = new Set(['RUNNING', 'PENDING', 'WAITING_APPROVAL', 'QUEUED']);
// 分析类任务的真实顺序是 finalize_execution → prepare_delivery（提交报告分支）；
// 源码修复任务才是交付 → 审批 → finalize。
const ANALYSIS_DELIVERY_ORDER = [
  'prepare_context', 'locate_agent', 'claim_repair_slot', 'repair_agent',
  'validate_changes', 'run_regression', 'review_agent',
  'finalize_execution', 'prepare_delivery', 'request_approval',
];

function businessResult(records: StepRecord[]): string {
  const failed = records.find(
    (record) => record.status === 'failed'
      || FAILED_RESULTS.has(String(record.result || '').toUpperCase()),
  );
  return failed ? String(failed.result || '').toUpperCase() : '';
}

function stageDuration(records: StepRecord[]): string {
  const total = records.reduce(
    (sum, record) => sum + (typeof record.duration_ms === 'number' && record.duration_ms > 0 ? record.duration_ms : 0),
    0,
  );
  if (total <= 0) return '';
  if (total < 1000) return '<1s';
  if (total < 60000) return `${(total / 1000).toFixed(total < 10000 ? 1 : 0)}s`;
  return `${Math.floor(total / 60000)}m${Math.round((total % 60000) / 1000)}s`;
}

function failureReason(records: StepRecord[]): string {
  const failed = records.find(
    (record) => record.status === 'failed'
      || FAILED_RESULTS.has(String(record.result || '').toUpperCase()),
  );
  if (!failed) return '';
  const summary = String(failed.summary || failed.error?.message || '').replace(/\s+/g, ' ').trim();
  if (!summary) return '';
  return summary.length > 116 ? `${summary.slice(0, 116).trimEnd()}…` : summary;
}

export function NodeRail({
  steps, selected, onSelect, task, recoveryError,
}: {
  steps: StepRecord[];
  selected: string;
  onSelect: (name: string) => void;
  task?: TaskDetailResponse['task'];
  recoveryError?: TaskDetailResponse['run_error'];
}) {
  const activeStage = stageForStep(selected)?.key;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ [activeStage ?? '']: true });

  const analysisDelivery = useMemo(() => {
    const finalize = stepRecord(steps, 'finalize_execution');
    const delivery = stepRecord(steps, 'prepare_delivery');
    const result = String(finalize?.result || '').toUpperCase();
    const finalizeAt = Date.parse(finalize?.finished_at || finalize?.started_at || '');
    const deliveryAt = Date.parse(delivery?.started_at || '');
    // 失败/转人工任务会先 finalize 生成报告，再由 after_finalize 提交报告分支；
    // 正常修复则是先交付再 finalize。用开始时间区分，避免把代码交付误判成报告交付。
    return Boolean(
      finalize && delivery
      && ['MANUAL', 'FAILED', 'INSUFFICIENT_EVIDENCE'].includes(result)
      && Number.isFinite(finalizeAt) && Number.isFinite(deliveryAt)
      && deliveryAt >= finalizeAt,
    );
  }, [steps]);

  const rows = useMemo<StageRow[]>(() => {
    const terminalStep = task?.report?.terminal_step || '';
    const taskResult = String(task?.result || '').toUpperCase();
    const taskActive = ACTIVE_TASK.has(taskResult);
    const terminalIndex = TASK_STAGES.findIndex((stage) => stage.members.includes(terminalStep));

    const base = TASK_STAGES.map((stage, index) => {
      const records = stage.members
        .map((name) => stepRecord(steps, name))
        .filter(Boolean) as StepRecord[];
      return {
        stage,
        index,
        status: stageStatus(stage, steps),
        progress: stageProgress(stage, steps),
        records,
        order: 0,
        reason: '',
        late: false,
      };
    });

    const lastExecuted = base.reduce((max, row) => (row.records.length ? Math.max(max, row.index) : max), -1);
    // 普通修复任务按静态阶段顺序；分析报告任务按 finalize → 报告交付的真实路由排序。
    const rank = (row: StageRow) => (
      analysisDelivery ? ANALYSIS_DELIVERY_ORDER.indexOf(row.stage.key) : row.index
    );
    const executed = base
      .filter((row) => row.records.length > 0)
      .sort((left, right) => rank(left) - rank(right));
    executed.forEach((row, position) => { row.order = position + 1; });

    for (const row of base) {
      if (row.records.length === 0) {
        if (terminalIndex >= 0 && row.index > terminalIndex && !taskActive) {
          row.reason = `任务在 ${terminalStep} 结束，未进入该阶段`;
        } else if (taskActive && row.index > lastExecuted) {
          row.reason = '尚未执行';
        } else {
          row.reason = '上游条件不满足，未进入该阶段';
        }
        continue;
      }
      const skipped = row.records.filter((record) => record.status === 'skipped');
      if (skipped.length) {
        row.reason = skipped[0].summary
          || (taskResult === 'MANUAL' || taskResult === 'INSUFFICIENT_EVIDENCE'
            ? '定位结论为转人工，跳过该阶段'
            : '上游路由判定无需执行该阶段');
      } else if (row.records.some((record) => record.status === 'waiting')) {
        row.reason = '等待人工审批';
      } else {
        row.reason = failureReason(row.records);
      }
      if (recoveryError && row.stage.key === 'request_approval') {
        row.reason = '后台恢复失败；审批中断仍保留，请在交付审批页重试';
      }
      if (analysisDelivery && row.stage.key === 'prepare_delivery' && !row.reason) {
        row.reason = '生成终局报告后提交分析报告分支';
      }
      // finalize_execution 负责写终态报告，分析类任务的报告交付也属于正常收尾；
      // 其他终态后的记录通常是重试/取消清理残留，单独标出。
      const analysisStage = analysisDelivery
        && ['finalize_execution', 'prepare_delivery', 'request_approval'].includes(row.stage.key);
      row.late = terminalIndex >= 0
        && row.index > terminalIndex
        && row.stage.key !== 'finalize_execution'
        && !analysisStage;
      if (row.late && !row.reason) {
        row.reason = '终态之后仍有记录，可能为重试或清理残留';
      }
    }
    const ordered = analysisDelivery
      ? [...base].sort((left, right) => rank(left) - rank(right))
      : base;
    // 执行过的节点按真实顺序排在前，未执行节点收敛到末尾分组，避免灰色节点
    // 插在真实路径中间造成“顺序错乱”的错觉。
    return [
      ...ordered.filter((row) => row.records.length > 0),
      ...ordered.filter((row) => row.records.length === 0),
    ];
  }, [steps, task, analysisDelivery, recoveryError]);

  const executedRows = rows.filter((row) => row.order > 0).sort((left, right) => left.order - right.order);
  const skippedCount = rows.filter((row) => row.records.length > 0 && row.status === 'skipped').length;
  const notRunCount = rows.filter((row) => row.records.length === 0).length;
  const abnormalCount = rows.filter(
    (row) => row.records.length > 0
      && (row.status === 'failed' || Boolean(businessResult(row.records))),
  ).length;
  const stageLabel = (stage: StageRow['stage']) => {
    if (!analysisDelivery) return stage.title;
    if (stage.key === 'finalize_execution') return '生成终局报告';
    if (stage.key === 'prepare_delivery') return '提交分析报告分支';
    return stage.title;
  };

  return (
    <nav className="node-rail" aria-label="任务步骤轨">
      <div className="node-rail-head">
        <span>任务节点</span>
        <span className="node-rail-count">{steps.length} 步</span>
      </div>

      {recoveryError && (
        <div className="node-recovery-warning" role="alert">
          <strong>审批恢复失败</strong>
          <span>{String(recoveryError.terminal_reason || recoveryError.message || '后台恢复执行失败。').replace(/\s+/g, ' ').slice(0, 180)}</span>
        </div>
      )}

      {executedRows.length > 0 && (
        <div className="node-summary">
          <span className="node-sum ok">执行 {executedRows.length}</span>
          {abnormalCount > 0 && <span className="node-sum bad">异常 {abnormalCount}</span>}
          {skippedCount > 0 && <span className="node-sum skip">跳过 {skippedCount}</span>}
          {notRunCount > 0 && <span className="node-sum idle">未执行 {notRunCount}</span>}
          {analysisDelivery && <span className="node-sum note">报告分支路由</span>}
        </div>
      )}

      <ol className="node-timeline">
        {rows.map(({ stage, status, progress, records, reason, order, late }, rowIndex) => {
          const isActive = activeStage === stage.key;
          const previous = rows[rowIndex - 1];
          // 连续未执行节点共享同一原因，只在第一行展示，避免多行重复文案。
          const showReason = Boolean(reason) && !(
            records.length === 0
            && previous?.records.length === 0
            && previous.reason === reason
          );
          const isOpen = expanded[stage.key] ?? isActive;
          const members = stage.members.map((name) => stepRecord(steps, name)).filter(Boolean) as StepRecord[];
          const canExpand = stage.group && members.length > 0;
          const display = records.length ? status : 'not_run';
          const business = businessResult(records);
          const duration = stageDuration(records);
          return (
            <Fragment key={stage.key}>
              {records.length === 0 && (rowIndex === 0 || rows[rowIndex - 1].records.length > 0) && (
                <li className="node-group-label">未执行节点 · {notRunCount}</li>
              )}
              <li
                className={'node-stage'
                  + (isActive ? ' is-active' : '')
                  + ' stage-' + display
                  + (!records.length ? ' is-not-run' : '')
                  + (late ? ' is-late' : '')}
              >
                <span className="node-dot" aria-hidden>{order > 0 ? order : ''}</span>
                <div className="node-card">
                  <button
                    type="button"
                    className="node-main"
                    onClick={() => onSelect(primaryStep(stage, steps))}
                  >
                    <span className="node-title-row">
                      <strong>{stageLabel(stage)}</strong>
                      {business ? (
                        <StatusBadge kind="task" value={business} compact />
                      ) : (
                        <StatusBadge kind="step" value={display} compact />
                      )}
                    </span>
                    <span className="node-meta">
                      {canExpand ? `${progress.done}/${progress.total} 成员步骤` : stage.members[0]}
                      {duration && ` · ${duration}`}
                    </span>
                    {business && records.every((record) => record.status === 'succeeded') && (
                      <span className="node-meta soft">节点执行成功，业务结论为 {business}</span>
                    )}
                    {showReason && reason !== `业务结果 ${business}` && (
                      <span className={'node-reason' + (late ? ' is-late' : '')} title={reason}>
                        {reason}
                      </span>
                    )}
                  </button>
                  {canExpand && (
                    <button
                      type="button"
                      className="node-expand"
                      aria-label={isOpen ? '收起成员步骤' : '展开成员步骤'}
                      onClick={() => setExpanded((current) => ({ ...current, [stage.key]: !isOpen }))}
                    >
                      {isOpen ? <DownOutlined /> : <RightOutlined />}
                    </button>
                  )}
                </div>
                {canExpand && isOpen && (
                  <ul className="node-members">
                    {members.map((record) => (
                      <li key={record.name}>
                        <button
                          type="button"
                          className={'node-member' + (selected === record.name ? ' is-selected' : '')}
                          onClick={() => onSelect(record.name)}
                        >
                          <span className={'node-member-dot status-' + record.status} />
                          <span className="node-member-name">{record.name}</span>
                          {AGENT_STEPS.has(record.name) && record.attempt ? (
                            <span className="node-attempt">a{record.attempt}</span>
                          ) : null}
                          {record.result && FAILED_RESULTS.has(String(record.result).toUpperCase()) ? (
                            <span className="node-member-result">{record.result}</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
