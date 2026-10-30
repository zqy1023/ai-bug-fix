import { Link, useParams } from 'react-router-dom';
import { taskResults, text } from '../lib/taskResults';
import type { TaskDetailResponse } from '../lib/types';
import { Badge, Card, CardBody, CardHead, Icon } from './ui';

export function TaskResults({ detail, analysis = true }: { detail: TaskDetailResponse; analysis?: boolean }) {
  const result = taskResults(detail);
  const { runId = '', taskId = '' } = useParams();
  const changed = detail.task.result === 'CHANGED_DELIVERED';
  const unchanged = detail.task.result === 'UNCHANGED_DELIVERED';
  const pending = detail.task.result === 'PENDING';
  const skipped = detail.task.result === 'SKIPPED';
  const attribution: Record<string, string> = { code: '代码问题', upstream: '上游响应', official: '外部服务或网络', mixed: '外部触发与本地缺陷', environment: '执行环境', undetermined: '尚未确定' };
  const changeTitle = changed ? '源码改动已交付' : unchanged ? '仅交付分析报告' : result.files.length ? '候选改动 · 尚未交付' : '尚无源码改动';
  const actionLabel: Record<string, string> = { update: '修改', add: '新增', delete: '删除' };

  return <div className={`task-results${analysis ? '' : ' repair-only'}`}>
    {analysis ? <Card id="problem-analysis" className="result-analysis">
      <CardHead title={<><span className="result-index">01</span>问题分析</>} actions={
        <Badge tone={result.analysisFailed ? 'danger' : result.analyzed ? 'info' : 'idle'}>
          {result.analysisFailed ? '分析未完成' : result.analyzed ? '已有分析结论' : pending ? '未开始分析' : '分析进行中'}
        </Badge>} />
      <CardBody>
        <div className="result-label">异常原因 {result.attribution ? <span className="tag">{attribution[result.attribution] || result.attribution}</span> : null}</div>
        <p className="result-prose">{result.rootCause || (result.analysisFailed ? result.failureReason || '分析执行失败，尚未取得异常归因。' : pending ? '等待任务执行后展示异常原因、分析依据与源码定位。' : '尚未取得明确的分析结论。')}</p>
        {result.facts.length ? <details className="result-details">
          <summary>分析依据 <span>{result.facts.length} 项</span></summary>
          <ul className="result-list">{result.facts.map((fact, i) => <li key={i}>{fact}</li>)}</ul>
        </details> : null}
        {result.sources.length ? <details className="result-details">
          <summary>源码定位 <span>{result.sources.length} 处</span></summary>
          <div className="source-findings">{result.sources.map((source, i) => <div key={i}>
            <div className="result-file">{text(source.path)}{Number(source.start_line) > 0 ? `:${source.start_line}` : ''}</div>
            {text(source.symbol) ? <code>{text(source.symbol)}</code> : null}
            <p>{text(source.finding) || text(source.note)}</p>
          </div>)}</div>
        </details> : null}
        {result.gaps.length ? <details className="result-details">
          <summary>分析阶段记录的证据缺口 <span>{result.gaps.length} 项</span></summary>
          <p className="note">保留分析时的证据范围；后续验证结果以独立审查为准。</p>
          <ul className="result-list">{result.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul>
        </details> : null}
      </CardBody>
    </Card> : null}

    <Card id="repair-result" className={`result-repair ${changed ? 'with-code' : unchanged ? 'report-only' : ''}`}>
      <CardHead title={<><span className="result-index">{analysis ? '02' : '01'}</span>修复结果</>}
        actions={<span className={`result-kind ${changed ? 'with-code' : unchanged ? 'report-only' : ''}`}><Icon name={changed || result.files.length ? 'code' : 'file'} size={14} />{changeTitle}</span>} />
      <CardBody>
        {skipped ? <div className="result-conclusion"><strong>已跳过修复与发布</strong>
          <p className="note">跳过表示本轮决定不继续修复或发布分支，具体执行过程见分析与审查记录。</p>
          <p className="result-prose">{result.skipReason || detail.task.terminal_reason || text(detail.task.report.terminal_reason) || '未记录具体跳过原因。'}</p></div> : null}
        {unchanged ? <p className="note">已交付分析报告，没有交付源码修改。{result.unresolvedIssues.length ? '下列已确认问题仍未修复。' : '报告交付不代表业务问题已恢复。'}</p> : null}
        <div className="result-label">{result.files.length ? '本次处理方式' : '处理结论'}</div>
        <p className="result-prose">{result.summary || result.rationale || (pending ? '尚未开始修复，当前没有代码改动。' : unchanged ? '已交付分析报告，没有交付源码修改。' : '尚未生成修复结果。')}</p>
        {result.unresolvedIssues.length ? <div className="unresolved-issues"><h3>仍未解决的问题</h3><ul className="result-list">{result.unresolvedIssues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div> : null}
        {result.edits.length ? <div className="edit-findings">{result.edits.map((edit, i) => <article className="edit-finding" key={i}>
          <div className="edit-heading"><span className="edit-operation">{actionLabel[text(edit.operation) || text(edit.action)] || '修改点'}</span><span className="result-file">{text(edit.path)}</span></div>
          {text(edit.symbol) ? <code className="edit-symbol">{text(edit.symbol)}</code> : null}
          <dl className="edit-explanation">
            {text(edit.problem) ? <><dt>原有问题</dt><dd>{text(edit.problem)}</dd></> : null}
            {text(edit.note) || text(edit.rationale) ? <><dt>具体修改</dt><dd>{text(edit.note) || text(edit.rationale)}</dd></> : null}
            {text(edit.note) && text(edit.rationale) && text(edit.note) !== text(edit.rationale) ? <><dt>修改依据</dt><dd>{text(edit.rationale)}</dd></> : null}
            {text(edit.risk) ? <><dt>影响边界</dt><dd>{text(edit.risk)}</dd></> : null}
            {text(edit.validation_notes) ? <><dt>修复方自检</dt><dd>{text(edit.validation_notes)}</dd></> : null}
          </dl>
        </article>)}</div> : result.rationale && result.rationale !== result.summary ? <p className="result-prose">{result.rationale}</p> : null}
        {result.files.length ? <div className="result-file-count"><span>{result.files.length} 个源码文件{changed ? '已改动交付' : '存在候选改动，尚未交付'}</span>
          <Link className="link" to={`/runs/${runId}/tasks/${encodeURIComponent(taskId)}/change`}>查看完整代码差异 →</Link></div> : null}
      </CardBody>
    </Card>
  </div>;
}
