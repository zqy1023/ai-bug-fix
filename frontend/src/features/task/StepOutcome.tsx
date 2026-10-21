import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { StepRecord } from '../../lib/types';
import { JsonViewer } from '../../components/JsonViewer';

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
  check_changes: '将实际差异与修改声明对账，检查范围、语法及敏感信息。',
  syntax_regression: '对修改后的 Python 源码执行语法检查。',
  verify_issue: '使用原问题证据执行独立验证；未重放时明确标记未验证。',
  run_regression: '在独立环境运行固定回归命令，保留退出码与输出。',
  review_agent: '独立审查差异、验证证据及剩余风险。',
  prepare_delivery: '提交修复文件、方案及验证脚本，记录分支与提交。',
  request_approval: '由人工复核已交付的分支并决定是否保留。',
  finalize_execution: '汇总任务结果、停止原因和后续处理要求。',
};
const labels: Record<string, string> = {
  root_cause: '根因分析', summary: '执行结论', reason: '处理说明', analysis: '修复分析',
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
  return String(step.error?.message || output.reason || output.summary || output.terminal_reason
    || output.root_cause || step.summary || descriptions[step.name] || '该步骤已记录，查看详情核对输出。');
}

export function StepOutcome({ step }: { step: StepRecord }) {
  const output = object(step.output);
  const entries = Object.entries(output).filter(([key, value]) => key in labels && value !== '' && value !== null
    && (!Array.isArray(value) || value.length > 0));
  return <div className="step-outcome">
    <p className="step-purpose">{descriptions[step.name] || '查看本步骤实际输入、处理结果及输出证据。'}</p>
    {step.error && <div className="step-error-message"><strong>{step.error.code}</strong><p>{step.error.message}</p></div>}
    {entries.length ? entries.map(([key, value]) => <section className="outcome-field" key={key}>
      <h4>{labels[key]}</h4>
      {typeof value === 'string' || typeof value === 'number' ? <p>{String(value)}</p>
        : Array.isArray(value) && value.every((item) => typeof item === 'string')
          ? <ul>{value.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
          : <JsonViewer value={value} defaultExpandDepth={2} />}
    </section>) : <p className="outcome-description">{outcomeText(step)}</p>}
    <div className="outcome-state">执行状态：{step.status} · 业务结果：{step.result || String(output.status || output.mode || '未单独声明')}</div>
  </div>;
}

export function VerificationEvidence({ runId, taskId, step }: { runId: string; taskId: string; step: StepRecord }) {
  const ref = step.artifacts?.find((item) => /(?:regression|verification)_report\.json$/.test(item.path));
  const query = useQuery({ queryKey: queryKeys.artifactContent(runId, ref?.path || '', taskId),
    queryFn: () => api.artifactContent(runId, ref!.path, taskId), enabled: Boolean(ref) });
  if (!['run_regression', 'verify_issue', 'syntax_regression'].includes(step.name)) return null;
  // 步骤记录用于列表摘要，可能只保留输出尾部；归档产物包含完整输出，必须优先。
  const report = { ...object(step.output), ...object(query.data?.content) };
  const commands = Array.isArray(report.commands) ? report.commands.map(object) : [];
  const replay = { ...report, ...object(report.replay) };
  const replayCommand = replay.replay_command;
  return <section className="verification-evidence">
    <h4>{step.name === 'verify_issue' ? '问题重放 · 实际结果' : '回归验证 · 实际执行'}</h4>
    {query.isLoading && <p>正在加载验证证据…</p>}
    {query.isError && <p role="alert">验证产物加载失败，请在产物页重试。</p>}
    {commands.map((command, index) => <article className="command-evidence" key={index}>
      <div><strong>命令 {index + 1}</strong><span>退出码 {String(command.returncode ?? '未知')}</span></div>
      <pre>{Array.isArray(command.command) ? command.command.map(String).join(' ') : String(command.command || '')}</pre>
      <h5>标准输出</h5><pre>{String(command.stdout || '（无输出）')}</pre>
      {Boolean(command.stderr) && <><h5>标准错误</h5><pre>{String(command.stderr)}</pre></>}
      {Boolean(command.truncated) && <p>当前为输出尾部，完整运行信息以归档产物为准。</p>}
    </article>)}
    {Array.isArray(replayCommand) && replayCommand.length > 0 && <><h5>实际重放命令</h5><pre>{replayCommand.map(String).join(' ')}</pre></>}
    {replay.replay_returncode != null && <p>重放退出码：{String(replay.replay_returncode)}</p>}
    {['replay_output', 'replay_error', 'replay_exception_type', 'replay_traceback'].map((key) => replay[key] ? <div key={key}><h5>{({replay_output: '重放输出', replay_error: '重放错误', replay_exception_type: '异常类型', replay_traceback: '异常堆栈'})[key]}</h5><pre>{String(replay[key])}</pre></div> : null)}
    {!commands.length && !replayCommand && <p>{String(report.reason || (step.name === 'syntax_regression' ? '本步骤执行 Python AST 语法检查，无 shell 命令。' : '尚无可核验的运行命令或重放结果，不能视为业务验证通过。'))}</p>}
  </section>;
}
