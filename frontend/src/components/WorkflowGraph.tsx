import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useMemo } from 'react';
import type { CSSProperties } from 'react';
import type { StepRecord, StepStatus } from '../lib/types';

const layout = [
  { id: 'discover_logs', label: '发现日志', x: 0, y: 0 },
  { id: 'normalize_error', label: '规范异常', x: 180, y: 0 },
  { id: 'deduplicate_error', label: '异常去重', x: 360, y: 0 },
  { id: 'identify_carrier', label: '识别运输商', x: 540, y: 0 },
  { id: 'claim_repair_slot', label: '领取资格', x: 760, y: 0 },
  { id: 'prepare_source_snapshot', label: '准备源码快照', x: 940, y: 0 },
  { id: 'prepare_evidence', label: '准备证据', x: 1120, y: 0 },
  { id: 'locate_agent', label: '定位根因', x: 1300, y: 0 },
  { id: 'prepare_workspace', label: '准备隔离区', x: 1480, y: 0 },
  { id: 'repair_agent', label: 'AI 修复', x: 1660, y: 0 },
  { id: 'check_changes', label: '检查变更', x: 1860, y: 0 },
  { id: 'verify_issue', label: '验证问题', x: 2060, y: 0 },
  { id: 'run_regression', label: '准生产回归', x: 2260, y: 0 },
  { id: 'review_agent', label: 'AI 审查', x: 2460, y: 0 },
  { id: 'request_approval', label: '人工审批', x: 2460, y: 150 },
  { id: 'apply_plan', label: '应用共享层方案', x: 2260, y: 150 },
  { id: 'prepare_delivery', label: '准备交付分支', x: 2060, y: 150 },
  { id: 'retry_prepare', label: '重试准备', x: 1860, y: 150 },
  { id: 'finalize_execution', label: '收尾与汇总', x: 1660, y: 150 },
] as const;

const transitions: Array<[string, string, string?]> = [
  ['discover_logs', 'normalize_error'], ['normalize_error', 'deduplicate_error'], ['deduplicate_error', 'identify_carrier'],
  ['identify_carrier', 'claim_repair_slot'], ['claim_repair_slot', 'prepare_source_snapshot', '通过'],
  ['claim_repair_slot', 'finalize_execution', '拒绝资格'], ['prepare_source_snapshot', 'prepare_evidence', '通过'],
  ['prepare_source_snapshot', 'finalize_execution', '失败'], ['prepare_evidence', 'locate_agent', '通过'],
  ['prepare_evidence', 'finalize_execution', '人工处理/失败'], ['locate_agent', 'prepare_workspace', '可修复'],
  ['locate_agent', 'finalize_execution', '转人工/失败'], ['prepare_workspace', 'repair_agent', '通过'],
  ['prepare_workspace', 'finalize_execution', '失败'], ['repair_agent', 'check_changes', '修复通过'],
  ['repair_agent', 'request_approval', '共享层授权'], ['repair_agent', 'retry_prepare', '修复失败'],
  ['check_changes', 'verify_issue', '通过'], ['check_changes', 'retry_prepare', '失败'],
  ['verify_issue', 'run_regression', '通过'], ['verify_issue', 'retry_prepare', '失败'],
  ['run_regression', 'review_agent', '通过'], ['run_regression', 'request_approval', '人工审查模式'],
  ['run_regression', 'retry_prepare', '失败'], ['review_agent', 'prepare_delivery', '审查通过'],
  ['review_agent', 'request_approval', '需要审批'], ['review_agent', 'retry_prepare', '审查失败'],
  ['review_agent', 'finalize_execution', '转人工'], ['request_approval', 'apply_plan', '批准共享层方案'],
  ['request_approval', 'prepare_delivery', '批准运输商修复'], ['request_approval', 'retry_prepare', '拒绝/重试'],
  ['request_approval', 'finalize_execution', '终止'], ['apply_plan', 'check_changes', '重新验证'],
  ['prepare_delivery', 'finalize_execution'], ['retry_prepare', 'repair_agent', '未超过重试上限'],
];

function nodeStyle(status?: StepStatus): CSSProperties {
  const palettes: Record<string, { background: string; border: string; color: string }> = {
    succeeded: { background: '#f0f9f4', border: '#78c79a', color: '#176b3a' },
    running: { background: '#eef4ff', border: '#6489ee', color: '#2449ad' },
    waiting: { background: '#fff7e6', border: '#e9b949', color: '#8b5e00' },
    failed: { background: '#fff1f0', border: '#f08b84', color: '#a61d24' },
    skipped: { background: '#f5f5f5', border: '#d9d9d9', color: '#777' },
  };
  const palette = palettes[status ?? ''];
  return { border: '1px solid ' + (palette?.border ?? '#d7dce5'), background: palette?.background ?? '#fff', color: palette?.color ?? '#344054', borderRadius: 8, padding: 10, width: 142, fontSize: 12, textAlign: 'center', boxShadow: '0 2px 6px rgba(20, 35, 70, .04)' };
}

function statusLabel(status?: StepStatus): string {
  const labels: Record<string, string> = {
    pending: '等待执行', running: '执行中', waiting: '等待人工处理',
    succeeded: '已完成', failed: '执行失败', skipped: '已跳过',
  };
  return labels[status ?? ''] ?? '未执行';
}

export function WorkflowGraph({ steps, selectedStep, onSelectStep }: { steps: StepRecord[]; selectedStep?: string; onSelectStep?: (name: string) => void }) {
  const { nodes, edges } = useMemo(() => {
    const records = new Map(steps.map((step) => [step.name, step]));
    const builtNodes: Node[] = layout.map(({ id, label, x, y }) => {
      const record = records.get(id);
      return { id, position: { x, y }, data: { label: <div><div>{label}</div><small>{statusLabel(record?.status)}</small></div> }, style: { ...nodeStyle(record?.status), borderWidth: selectedStep === id ? 2 : 1, borderColor: selectedStep === id ? '#456de0' : undefined, boxShadow: selectedStep === id ? '0 0 0 3px rgba(69,109,224,.14)' : undefined }, };
    });
    const builtEdges: Edge[] = transitions.map(([source, target, label], index) => ({
      id: source + '-' + target + '-' + index,
      source,
      target,
      label,
      animated: records.get(source)?.status === 'running',
      style: { stroke: records.get(source)?.status === 'succeeded' ? '#5cbd85' : '#c6ccd7', strokeWidth: 1.4 },
      labelStyle: { fill: '#667085', fontSize: 10 },
      labelBgStyle: { fill: '#fbfcfe' },
    }));
    return { nodes: builtNodes, edges: builtEdges };
  }, [steps, selectedStep]);
  return <div className="workflow-canvas"><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={true} onNodeClick={(_, node) => onSelectStep?.(node.id)} proOptions={{ hideAttribution: true }}><Background color="#e7eaf0" gap={20} /><MiniMap pannable zoomable /><Controls /></ReactFlow></div>;
}