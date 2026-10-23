import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { StepRecord } from '../../lib/types';
import { JsonViewer } from '../../components/JsonViewer';
import { ReviewResult } from './ReviewResult';
import { ReplayLogs } from './ReplayLogs';
import { evidenceWarning, ReplayComparison, TextEvidence } from './ReplayEvidence';

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
  run_regression: '将日志请求和代理渠道传入 server_dev.main，核对实际业务结果，并执行配置的补充命令。',
  review_agent: '独立审查差异、验证证据及剩余风险。',
  prepare_delivery: '提交业务修改与本轮任务概要，记录分支与提交。',
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
  if (step.name === 'review_agent') return <ReviewResult step={step} />;
  const output = object(step.output);
  const entries = Object.entries(output).filter(([key, value]) => key in labels && value !== '' && value !== null
    && (!Array.isArray(value) || value.length > 0));
  return <div className="step-outcome">
    <details className="step-function-note"><summary>节点职责</summary><p>{descriptions[step.name] || '查看本步骤实际输入、处理结果及输出证据。'}</p></details>
    {step.error && <div className="step-error-message"><strong>{step.error.code}</strong><p>{step.error.message}</p></div>}
    {entries.length ? entries.map(([key, value]) => <section className="outcome-field" key={key}>
      <h4>{labels[key]}</h4>
      {typeof value === 'string' || typeof value === 'number' ? <p>{String(value)}</p>
        : Array.isArray(value) && value.every((item) => typeof item === 'string')
          ? <ul>{value.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
          : <JsonViewer value={value} defaultExpandDepth={2} />}
    </section>) : !step.error && <p className="outcome-description">{typeof output.passed === 'boolean' ? (output.passed ? '检查通过' : '检查未通过') : step.summary || '该节点没有单独记录结论，可查看原始输出。'}</p>}
    <div className="outcome-state">执行状态：{step.status} · 业务结果：{step.result || String(output.status || output.mode || '未单独声明')}</div>
  </div>;
}

export function VerificationEvidence({ runId, taskId, step }: { runId: string; taskId: string; step: StepRecord }) {
  const reportName = step.name === 'run_regression' ? 'regression_report.json' : 'verification_report.json';
  const ref = step.artifacts?.find((item) => item.path.replaceAll('\\', '/').split('/').at(-1) === reportName);
  const query = useQuery({ queryKey: queryKeys.artifactContent(runId, ref?.path || '', taskId),
    queryFn: () => api.artifactContent(runId, ref!.path, taskId), enabled: Boolean(ref) });
  if (!['run_regression', 'verify_issue', 'syntax_regression'].includes(step.name)) return null;
  // 步骤记录用于列表摘要，可能只保留输出尾部；归档产物包含完整输出，必须优先。
  const report = { ...object(step.output), ...object(query.data?.content) };
  const commands = Array.isArray(report.commands) ? report.commands.map(object) : [];
  const request = object(report.request_regression);
  const requestInput = object(request.input);
  const requestItems = Array.isArray(request.items) ? request.items.map(object) : [];
  const businessObservation = object(request.business_observation);
  const businessErrors = Array.isArray(businessObservation.errors)
    ? businessObservation.errors.map(object).filter((item) => item.business_related !== false) : [];
  const supplemental = object(report.supplemental_verification);
  // 重放日志正文在产物里是全文，在步骤记录里是审计投影的截断版：产物优先，
  // 否则 report.replay 的截断值会盖掉 report 里同一字段的完整值。
  const replay = { ...object(report.replay), ...report };
  const replayCommand = replay.replay_command;
  const comparison = object(replay.replay_comparison);
  const verification = object(report.verification);
  const assertions = Array.isArray(verification.assertions) ? verification.assertions.map(object) : [];
  const warning = evidenceWarning(replay);
  const limitations = Array.isArray(verification.limitations) ? verification.limitations.map(String) : [];
  const inputMode = String(replay.replay_input_mode || comparison.input_mode || '未知');
  const verdict = String(replay.replay_verdict || report.status || '尚无结果');
  const verdictLabel = ({ exception_handled: '原异常已改为受控处理，待审查业务归类依据', fixed: '原异常消除，已返回业务结果',
    not_fixed: '仍复现原异常', new_error: '出现其他错误', regression: '发现行为回退', not_reproduced: '未复现原问题',
    input_not_verified: '输入送达未验证', missing_evidence: '缺少原始证据', environment: '执行环境受阻' } as Record<string, string>)[verdict] || verdict;
  return <section className="verification-evidence">
    <h4>{step.name === 'verify_issue' ? '问题重放 · 实际结果' : '回归验证 · 实际执行'}</h4>
    {query.isLoading && <p>正在加载验证证据…</p>}
    {query.isError && <p role="alert">验证产物加载失败，请在产物页重试。</p>}
    {step.name === 'verify_issue' && <>
      <p><strong>验证目标：</strong>{String(verification.goal || '旧记录未明确验证目标，不能据此推断覆盖范围')}</p>
      <p>输入来源：{({ recorded: '已录制的原始响应', live: '在线请求（响应不固定）', missing_response: '缺少录制响应' } as Record<string, string>)[inputMode] || inputMode} · 判定：{warning ? '证据失效，需重新验证' : verdictLabel}</p>
      {assertions.length > 0 && <ul>{assertions.map((item) => <li key={String(item.key)}>{String(item.label)}：{warning ? '需重跑确认' : ({ passed: '通过', failed: '未通过', unverified: '未验证' } as Record<string, string>)[String(item.status)] || String(item.status)}</li>)}</ul>}
      <ReplayComparison replay={replay} />
      <p className="replay-result-reason">{warning ? '历史判定原因' : '判定原因'}：{String(report.reason || replay.reason || '尚无判定说明')}</p>
      {limitations.length > 0 && <div><strong>本次覆盖边界</strong><ul>{limitations.map((value) => <li key={value}>{value}</li>)}</ul></div>}
    </>}
    {step.name === 'run_regression' && Boolean(request.entrypoint) && <section>
      <p><strong>请求入口：</strong>{String(request.entrypoint)} · {String(request.reason || '尚无结论')}</p>
      <p><strong>运输商：</strong>{String(requestInput.carrier || '缺失')} · 本次实时请求的结果不替代原异常修复前后对比。</p>
      {requestItems.map((item, index) => <p key={index}>单号 {String(item.request_number || '缺失')}：{String(item.error_type || '未知')} · 轨迹数 {String(item.event_count ?? 0)}{Boolean(item.error_msg) && ` · ${String(item.error_msg)}`}</p>)}
      {businessErrors.length > 0 && <div role="alert"><strong>框架业务错误 · 返回 Succeed 也不能视为通过</strong>
        {businessErrors.map((item, index) => <p key={index}>{String(item.category || '未分类')}：{String(item.message || '')}</p>)}
      </div>}
      <details><summary>实际请求参数</summary><TextEvidence text={JSON.stringify(requestInput, null, 2)} name="request-input.json" /></details>
      <details><summary>本次验证脚本</summary><TextEvidence text={String(request.script || '尚未生成')} name="request_regression.py" /></details>
      <details><summary>原始响应与业务结果</summary>
        <h5>本次实际响应正文</h5><TextEvidence text={String(request.response ?? '本次未取得响应正文')} name="request-response.txt" />
        <details><summary>完整业务返回</summary><TextEvidence text={JSON.stringify(request.result, null, 2) || '没有有效业务返回'} name="request-result.json" /></details>
      </details>
    </section>}
    {step.name === 'run_regression' && Boolean(supplemental.status) && <section>
      <h5>新采集正文 · 补充双侧验证</h5>
      <p>样本来自本次真实请求，属于独立补充证据。原始验证记录保留。</p>
      <p>{String(supplemental.reason || supplemental.status)}</p>
      {Boolean(object(supplemental.sample_source).sha256) && <p>样本 SHA256：<code>{String(object(supplemental.sample_source).sha256)}</code></p>}
      <p>完整前后结果及输入证据见产物 supplemental_verification_report.json。</p>
    </section>}
    {commands.map((command, index) => <details className="command-evidence" key={index}>
      <summary>执行记录 {index + 1} · 退出码 {String(command.returncode ?? '未知')} · 查看命令和完整输出</summary>
      <div><strong>命令 {index + 1}</strong><span>退出码 {String(command.returncode ?? '未知')}</span></div>
      <details><summary>命令原文</summary><TextEvidence text={JSON.stringify(command.command, null, 2) || ''} name={`command-${index + 1}.json`} /></details>
      <h5>标准输出</h5><TextEvidence text={String(command.stdout || '（无输出）')} name={`stdout-${index + 1}.txt`} />
      {Boolean(command.stderr) && <><h5>标准错误</h5><TextEvidence text={String(command.stderr)} name={`stderr-${index + 1}.txt`} /></>}
      {Boolean(command.truncated) && <p>当前为输出尾部，完整运行信息以归档产物为准。</p>}
    </details>)}
    {step.name === 'verify_issue' && <ReplayLogs replay={replay} runId={runId} taskId={taskId} artifactPath={ref?.path} />}
    {!commands.length && !replayCommand && <p>{String(report.reason || (step.name === 'syntax_regression' ? '本步骤执行 Python AST 语法检查，无 shell 命令。' : '尚无可核验的运行命令或重放结果，不能视为业务验证通过。'))}</p>}
  </section>;
}
