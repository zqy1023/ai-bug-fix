import { useMemo } from 'react';
import { AlertOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import type { TaskDetailResponse } from '../../lib/types';
import { dict, getText, objects, shortText, strings, stepSummary, type TaskView } from './taskVerdict';
import { Fold, Section as TaskSection, SectionEmpty, ToneTag } from '../../components/section/Section';
import { useProblemLog } from '../../hooks/useProblemLog';
import styles from './TaskSections.module.css';

const SCOPE_LABELS: Record<string, string> = { carrier: '单一承运商', project: '项目 / 共享层' };

/** 根因分区：结论直接铺开，证据按需展开；原始异常固定在右侧便于对照。 */
export function RootCauseSection({ detail, view, onOpenStep }: {
  detail: TaskDetailResponse;
  view: TaskView;
  onOpenStep: (name: string) => void;
}) {
  const step = view.locateStep;
  const output = dict(step?.output);
  const rootCause = getText(output, 'root_cause', 'analysis');
  const summary = getText(output, 'summary') || stepSummary(step) || getText(dict(detail.task.report), 'root_cause');
  const category = getText(output, 'category');
  const scope = getText(output, 'recommended_scope');
  const facts = strings(output.facts);
  const evidence = useMemo(() => objects(output.source_evidence), [output]);
  const gaps = strings(output.evidence_gaps);
  const findingCount = facts.length + evidence.length + gaps.length;

  return (
    <TaskSection
      title="根因分析"
      subtitle="先看结论，证据按需展开"
      extra={category && !['carrier', 'unknown', 'none', 'null', '-'].includes(category.toLowerCase()) ? <ToneTag tone="info">{category}</ToneTag> : undefined}
    >
      <div className={styles.rootGrid}>
        <div className={styles.rootMain}>
          {rootCause || summary || findingCount > 0 ? (
            <>
              <p className={styles.rootCause}>{shortText(rootCause || summary || '尚未生成根因结论。', 400)}</p>
              {(rootCause || summary) && (rootCause.length > 400 || summary !== rootCause || findingCount > 0) && (
                <Fold summary="查看完整分析与证据" count={`${findingCount} 项`}>
                  {rootCause.length > 400 && (
                    <div className={styles.block}><label>完整根因</label><p>{rootCause}</p></div>
                  )}
                  {summary && summary !== rootCause && (
                    <div className={styles.block}><label>定位摘要</label><p>{summary}</p></div>
                  )}
                  {facts.length > 0 && (
                    <div className={styles.block}><label>定位依据（{facts.length}）</label><ul>{facts.map((fact, index) => <li key={`${index}-${fact}`}>{fact}</li>)}</ul></div>
                  )}
                  {evidence.length > 0 && (
                    <div className={styles.block}>
                      <label>源码证据（{evidence.length}）</label>
                      {evidence.map((item, index) => (
                        <div className={styles.sourceEvidence} key={`${getText(item, 'path')}-${index}`}>
                          <code>{getText(item, 'path') || '源码位置未记录'}{getText(item, 'symbol') ? ` · ${getText(item, 'symbol')}` : ''}</code>
                          {getText(item, 'finding') && <p>{getText(item, 'finding')}</p>}
                        </div>
                      ))}
                    </div>
                  )}
                  {gaps.length > 0 && (
                    <div className={styles.block}><label>证据缺口（{gaps.length}）</label><ul>{gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></div>
                  )}
                </Fold>
              )}
              <div className={styles.footRow}>
                {scope && <ToneTag tone="muted">建议范围 · {SCOPE_LABELS[scope.toLowerCase()] || scope}</ToneTag>}
                {step && <Button size="small" type="link" onClick={() => onOpenStep('locate_agent')}>查看定位步骤 <ArrowRightOutlined /></Button>}
              </div>
            </>
          ) : (
            <SectionEmpty
              title={step ? '定位步骤已执行，但没有产出根因结论' : '定位步骤未执行'}
              body={step ? '该步骤的输入输出中没有 root_cause 或 summary 字段。' : '本任务没有进入定位阶段，因此没有定位记录。'}
            />
          )}
        </div>

        <aside className={styles.exception} aria-label="原始异常">
          <div className={styles.exceptionHead}><AlertOutlined aria-hidden="true" />原始异常</div>
          <ExceptionRows detail={detail} />
        </aside>
      </div>
    </TaskSection>
  );
}

function ExceptionRows({ detail }: { detail: TaskDetailResponse }) {
  const problem = dict(detail.task.problem);
  // 完整日志走共享 Hook：历史任务的步骤记录被裁剪过，会回读 run 级去重产物。
  const log = useProblemLog(detail);
  const service = typeof problem.service_name === 'string' ? problem.service_name : '';
  const biztype = typeof problem.biztype === 'string' ? problem.biztype : '';
  if (!log.message && !service) {
    return <p className={styles.exceptionEmpty}>该任务没有保存原始异常信息。</p>;
  }
  return (
    <>
      {service && <div className={styles.exceptionRow}><b>服务</b><code>{service}</code></div>}
      {biztype && <div className={styles.exceptionRow}><b>业务</b><span>{biztype}</span></div>}
      {log.message && (
        <div className={styles.exceptionRow}>
          <b>日志</b>
          <pre className={styles.exceptionText}>{log.message}</pre>
        </div>
      )}
      {log.extracted && log.extracted !== log.message && (
        <Fold summary="从日志中提取的 error 字段" tone="warn">
          <pre className={styles.exceptionText}>{log.extracted}</pre>
        </Fold>
      )}
    </>
  );
}
