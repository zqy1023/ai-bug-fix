import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useMemo } from 'react';
import type { StepRecord, StepStatus } from '../lib/types';
const layout = [
  ['discover_logs', '发现日志'], ['normalize_error', '规范异常'], ['deduplicate_error', '异常去重'], ['identify_carrier', '识别运输商'],
  ['claim_repair_slot', '领取资格'], ['prepare_source_snapshot', '准备源码快照'], ['prepare_evidence', '准备证据'], ['locate_agent', '定位根因'],
  ['prepare_workspace', '准备隔离区'], ['repair_agent', 'AI 修复'], ['check_changes', '检查变更'], ['verify_issue', '验证问题'],
  ['run_regression', '准生产回归'], ['review_agent', 'AI 审查'], ['request_approval', '人工审批'], ['apply_plan', '应用共享层方案'],
  ['prepare_delivery', '准备交付'], ['finalize_execution', '收尾'],
] as const;
function nodeStyle(status?: StepStatus): React.CSSProperties {
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
export function WorkflowGraph({ steps }: { steps: StepRecord[] }) {
  const { nodes, edges } = useMemo(() => {
    const records = new Map(steps.map((step) => [step.name, step]));
    const cols = 4;
    const builtNodes: Node[] = layout.map(([id, label], index) => {
      const record = records.get(id); const row = Math.floor(index / cols); const column = row % 2 === 0 ? index % cols : cols - 1 - (index % cols);
      return { id, position: { x: column * 190, y: row * 108 }, data: { label: <div><div>{label}</div><small>{record ? record.status : '未执行'}</small></div> }, style: nodeStyle(record?.status) };
    });
    const builtEdges: Edge[] = layout.slice(0, -1).map(([id], index) => ({
      id: id + '-' + layout[index + 1][0], source: id, target: layout[index + 1][0],
      animated: records.get(id)?.status === 'running',
      style: { stroke: records.get(id)?.status === 'succeeded' ? '#5cbd85' : '#c6ccd7', strokeWidth: 1.5 },
    }));
    return { nodes: builtNodes, edges: builtEdges };
  }, [steps]);
  return <div className="workflow-canvas"><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}><Background color="#e7eaf0" gap={20} /><MiniMap pannable zoomable /><Controls /></ReactFlow></div>;
}