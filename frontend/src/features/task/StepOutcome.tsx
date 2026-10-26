import type { StepRecord } from '../../lib/types';
import { JsonViewer } from '../../components/JsonViewer';
import { ReviewResult } from './ReviewResult';
import { ValidationResult } from './ValidationResult';
import styles from './StepOutcome.module.css';

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue => value && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {};
const descriptions: Record<string, string> = {
  prepare_source_snapshot: '建立修复前源码基线，后续差异以此核验。',
  repair_branch_preflight: '核查已有分支，避免覆盖或重复交付。',
  prepare_workspace: '建立目标运输商的隔离修改目录。',
  prepare_evidence: '识别实际框架、入口与候选源码。',
  locate_agent: '结合异常与源码定位根因，区分事实、推断与证据缺口。',
  claim_repair_slot: '领取本问题的修复资格，防止重复处理。',
  repair_agent: '执行运输商范围内的修复，记录方案及实际修改清单。',
  review_agent: '内部执行范围、语法和业务验证，按三个维度综合判定修复效果。',
  prepare_delivery: '提交业务修改与本轮任务概要，记录本地分支与候选提交。',
  request_approval: '交付后由人工核对；确认保留分支，或打回撤回并回修。',
  define_acceptance: '修改前固定验收断言，并绑定原始问题和完整基线。',
  reproduce_baseline: '只运行未修改代码，确认原始异常能够复现。',
  prepare_analysis: '生成分析报告，交付完成后统一收尾。',
  publish_delivery: '将准备阶段的同一提交正式交付，按配置推送。',
  finalize_execution: '汇总任务结果、停止原因和后续处理要求。',
  plan_validation: '固定本次验证的覆盖范围、原始响应可用性与验收断言。',
  summarize_validation: '汇总原异常重放与真实请求回归两个维度的实际结论。',
};
const labels: Record<string, string> = {
  root_cause: '根因分析', summary: '执行结论', reason: '处理说明', analysis: '修复分析',
  contract_id: '验收契约标识', assertions: '验收断言', candidate_id: '候选标识',
  binding: '证据身份', report_path: '证据报告',
  terminal_reason: '结束原因', blocked_reason: '阻断原因', next_action: '下一步',
  facts: '事实依据', source_evidence: '源码证据', evidence_gaps: '证据缺口',
  changed_files: '修改文件', checked_files: '已检查文件', candidates: '调查文件',
  findings: '审查发现', required_changes: '待处理事项', failures: '未通过检查',
  change_plan: '修复方案', scope: '范围检查', claim: '修改声明核验', totals: '变更统计',
  repair_branch: '交付分支', commit: '提交', decision: '审批决定',
  command_count: '命令数量', record_count: '处理记录', unique_count: '独立问题',
};

export function outcomeText(step: StepRecord): string {
  const output = object(step.output);
  return String(step.error?.message || step.assessment?.summary || output.reason || output.summary || output.terminal_reason
    || output.root_cause || step.summary || descriptions[step.name] || '该步骤已记录，查看详情核对输出。');
}

export function StepOutcome({ step }: { step: StepRecord }) {
  if (step.name === 'decide_validation') return <ValidationResult step={step} />;
  // 审查内部验证步骤同样用统一结论渲染，缺失数据时给出明确的不可用态。
  if (step.name === 'plan_validation' || step.name === 'summarize_validation') return <ValidationResult step={step} />;
  if (step.name === 'review_agent') return <ReviewResult step={step} />;
  const output = object(step.output);
  const entries = Object.entries(output).filter(([key, value]) => key in labels && value !== '' && value !== null
    && (!Array.isArray(value) || value.length > 0));
  return <div className={styles.outcome}>
    <details className={styles.functionNote}><summary>节点职责</summary><p>{descriptions[step.name] || '查看本步骤实际输入、处理结果及输出证据。'}</p></details>
    {step.error && <div className={styles.errorMessage}><strong>{step.error.code}</strong><p>{step.error.message}</p></div>}
    {entries.length ? entries.map(([key, value]) => <section className={styles.field} key={key}>
      <h4>{labels[key]}</h4>
      {typeof value === 'string' || typeof value === 'number' ? <p>{String(value)}</p>
        : Array.isArray(value) && value.every((item) => typeof item === 'string')
          ? <ul>{value.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
          : <JsonViewer value={value} defaultExpandDepth={2} />}
    </section>) : !step.error && <p className={styles.stateDescription}>{typeof output.passed === 'boolean' ? (output.passed ? '检查通过' : '检查未通过') : step.summary || '该节点没有单独记录结论，可查看原始输出。'}</p>}
    <div className={styles.state}>执行状态：{step.execution?.status ?? step.status} · 业务结论：{step.assessment?.outcome ?? step.result ?? String(output.status || '未声明')}</div>
  </div>;
}

