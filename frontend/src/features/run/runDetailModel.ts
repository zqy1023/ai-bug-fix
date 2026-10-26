import type { RunDetail, StepRecord, StepStatus } from '../../lib/types';

const phaseDefinitions = [
  { key: 'source', title: '同步源码', description: '建立本轮源码基线', members: ['sync_source_repo'] },
  { key: 'discovery', title: '发现问题', description: '采集、规范化与去重', members: ['discover_logs', 'normalize_error', 'deduplicate_error'] },
  { key: 'identify', title: '识别任务', description: '确定承运商与处理范围', members: ['identify_carrier'] },
  { key: 'tasks', title: '修复与验证', description: '进入任务工作区核验每个问题', members: [] },
  { key: 'collect', title: '汇总结果', description: '记录本轮最终处理结果', members: [] },
];

export interface RunPhase {
  key: string; title: string; description: string; records: StepRecord[]; status: StepStatus;
}

/** 展示适配器：保留服务端终态，不把 checkpoint 的恢复位置解释为活跃执行。 */
export function runDetailModel(run: RunDetail) {
  const stopped = run.terminal === true;
  const failureStep = run.run_error?.terminal_step || run.run_report?.terminal_step || '';
  const current = run.run_lifecycle?.current_step || run.phase || '';
  const phases: RunPhase[] = phaseDefinitions.map((definition) => {
    const records = run.steps.filter((step) => definition.members.includes(step.name));
    let status: StepStatus = stopped ? 'skipped' : 'pending';
    if (records.length) {
      status = records.some((step) => step.status === 'failed') ? 'failed'
        : records.some((step) => step.status === 'running') && !stopped ? 'running'
          : records.some((step) => step.status === 'waiting') ? 'waiting'
            : records.every((step) => ['succeeded', 'skipped'].includes(step.status)) ? 'succeeded' : 'pending';
    } else if (!stopped && definition.members.includes(current)) status = 'running';
    if (definition.key === 'tasks' && run.task_count > 0) {
      status = run.waiting_tasks > 0 ? 'waiting' : !stopped ? 'running' : run.failed_tasks > 0 ? 'failed' : 'succeeded';
    }
    if (definition.key === 'collect' && stopped && !run.run_error?.message) {
      status = run.status === 'succeeded' ? 'succeeded' : run.status === 'cancelled' ? 'skipped' : ['failed', 'partial_failed'].includes(run.status) ? 'failed' : 'pending';
    }
    return { ...definition, records, status };
  });
  const focus = phases.find((phase) => phase.records.some((step) => step.name === failureStep))
    || phases.find((phase) => phase.status === 'running') || phases.find((phase) => phase.status === 'waiting')
    || phases.find((phase) => phase.status === 'failed') || phases[0];
  const titles: Record<string, string> = {
    failed: '本轮执行未完成', partial_failed: '部分任务需要处理', succeeded: '本轮执行已完成',
    paused: '执行已暂停', cancelled: '执行已停止', waiting_approval: '修复候选等待审批',
    queued: '正在等待执行', running: '执行正在推进', pause_requested: '正在等待安全暂停', cancel_requested: '正在停止执行',
  };
  return {
    phases, focus: focus.key, title: titles[run.status] || '执行详情',
    reason: run.run_error?.message || run.run_report?.terminal_reason || run.run_lifecycle?.progress?.message || '',
    nextAction: run.run_error?.next_action || run.run_report?.next_action || '',
    failureLabel: phases.find((phase) => phase.records.some((step) => step.name === failureStep))?.title || failureStep,
    recoveryNode: run.checkpoint?.next_nodes?.[0] || '',
  };
}

/** Short presentation for operational failures; the archived text remains available in evidence. */
export function readableRunFailure(message: string, source = false): string {
  if (source && /Connection was reset|connection reset|Recv failure/i.test(message)) return '远程源码仓库的连接被重置，源码同步未完成。';
  if (source && /timed? out|timeout/i.test(message)) return '连接远程源码仓库超时，未能完成源码同步。';
  if (source && /authentication failed|401|403|access denied|permission denied/i.test(message)) return '远程源码仓库拒绝访问，请核对账号权限和访问凭据。';
  if (source && /certificate|TLS|SSL/i.test(message)) return '连接远程源码仓库时出现证书或 TLS 错误，请核对连接环境。';
  if (source) return '源码同步未完成，请查看完整日志确认具体原因。';
  const first = message.split(/\r?\n/)[0].trim();
  return first.length > 160 ? '该阶段执行未完成，具体原因已保存在执行日志中。' : first || '该阶段执行未完成。';
}
