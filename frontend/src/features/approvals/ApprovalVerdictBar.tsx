import { AlertOutlined, BranchesOutlined, DiffOutlined, ExperimentOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import { formatTime } from '../../lib/format';
import type { ApprovalItem } from '../../lib/types';
import { Fold, ToneTag } from '../../components/section/Section';
import type { ApprovalView } from './useApprovalView';
import styles from './ApprovalVerdictBar.module.css';

/**
 * 审批结论条：一眼看清「改了什么 / 验证是否可信 / 交付到哪」，再决定保留或打回。
 * 指标位不可点（审批只有一个决策动作），只做信息呈现。
 */
export function ApprovalVerdictBar({ item, view }: { item: ApprovalItem; view: ApprovalView }) {
  const delivered = item.delivery?.phase === 'published';
  const prepared = item.delivery?.phase === 'prepared';
  const tone = view.recoveryFailure ? 'bad'
    : view.files.length === 0 ? 'warn'
      : !view.gateEvidence ? 'warn'
        : view.verdict === 'FAIL' ? 'bad'
          : view.checks.some((check) => check.state === 'failed') ? 'bad' : 'ok';

  const changeValue = view.gateEvidence
    ? `${view.files.length} 个实际变更文件`
    : view.files.length ? `${view.files.length} 个声明文件（待核验）` : '无变更文件';
  const verifyValue = view.checks.every((check) => check.state === 'passed') ? '三维审查通过'
    : view.checks.some((check) => check.state === 'failed') ? '审查未通过'
      : view.checks.some((check) => check.state === 'unknown') ? '审查证据不完整' : '尚未审查';

  return (
    <section className={`${styles.bar} ${styles[`bar${tone[0].toUpperCase()}${tone.slice(1)}`]}`} aria-label="审批结论">
      <div className={styles.head}>
        <h2 className={styles.title}>{item.carrier || '未识别承运商'} · {item.biztype || '未知业务'}</h2>
        <Tag color={item.scope === 'carrier' ? 'blue' : 'orange'}>{view.scopeText}</Tag>
        <ToneTag tone={view.recoveryTone}>业务恢复 · {view.recoveryText}</ToneTag>
        <span className={styles.meta}>
          <span>申请 <b>{formatTime(item.created_at)}</b></span>
          {item.expires_at && <span>到期 <b>{formatTime(item.expires_at)}</b></span>}
        </span>
      </div>
      <p className={styles.desc}>{item.summary || '结合原始异常、实际代码差异与验证证据进行核对。'}</p>

      {view.recoveryFailure && (
        <div className={`${styles.notice} ${styles.noticeBad}`} role="alert">
          <AlertOutlined aria-hidden="true" />
          <span><b>上次审批恢复失败</b>{view.recoveryFailure}　修复运行环境后可重新提交决策；恢复失败不会被当成已重跑。</span>
        </div>
      )}
      {!view.gateEvidence && view.files.length > 0 && (
        <div className={`${styles.notice} ${styles.noticeWarn}`} role="status">
          <AlertOutlined aria-hidden="true" />
          <span><b>文件清单来自方案声明</b>未取得确定性门禁证据，逐行差异仍需在下方核对后再决定是否保留。</span>
        </div>
      )}

      <div className={styles.tiles}>
        <div className={styles.tile}>
          <span className={styles.tileLabel}><DiffOutlined />代码改动</span>
          <span className={`${styles.tileValue} ${view.gateEvidence ? styles.toneOk : styles.toneWarn}`}>{changeValue}</span>
          <span className={styles.tileSub}>{view.added || view.removed ? `+${view.added} / −${view.removed}` : '无增删统计'}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}><SafetyCertificateOutlined />AI 审查</span>
          <span className={`${styles.tileValue} ${verifyValue.includes('通过') && !verifyValue.includes('未通过') ? styles.toneOk : verifyValue.includes('未通过') ? styles.toneBad : styles.toneWarn}`}>{verifyValue}</span>
          <span className={styles.tileSub}>{view.findings.length ? `${view.findings.length} 项发现` : '无审查发现'}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}><ExperimentOutlined />业务恢复</span>
          <span className={`${styles.tileValue} ${view.recoveryTone === 'ok' ? styles.toneOk : view.recoveryTone === 'bad' ? styles.toneBad : styles.toneWarn}`}>{view.recoveryText}</span>
          <span className={styles.tileSub}>{item.business_recovery?.reason ? '已记录原因' : '无原因记录'}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}><BranchesOutlined />Git 交付</span>
          <span className={`${styles.tileValue} ${delivered ? styles.toneOk : prepared ? styles.toneWarn : styles.toneMuted}`}>
            {delivered ? '已交付 · 待决策' : prepared ? '本地候选已准备' : '未确认交付'}
          </span>
          <span className={styles.tileSub}>{item.delivery?.commit ? item.delivery.commit.slice(0, 10) : '无提交'}</span>
        </div>
      </div>

      {/*
        完整原始日志 message 必须默认可见且不被截断：审批人要判断"这次改动是否真的解决了报错"，
        脱离原始日志就只能盲审。提取出的 error 字段单独标注，不冒充日志全文。
      */}
      <div className={styles.exception} role="region" aria-label="原始异常日志">
        <div className={styles.exceptionHead}>
          <AlertOutlined aria-hidden="true" />
          <span>原始异常日志</span>
          {view.serviceName && <code className={styles.exceptionService}>{view.serviceName}</code>}
        </div>
        {view.loading ? (
          <p className={styles.exceptionEmpty}>任务详情读取中…</p>
        ) : view.originalMessage ? (
          <>
            <pre className={styles.exceptionFull}>{view.originalMessage}</pre>
            {view.originalError && view.originalError !== view.originalMessage && (
              <Fold summary="查看从日志中提取的 error 字段">
                <pre className={styles.exceptionFull}>{view.originalError}</pre>
              </Fold>
            )}
          </>
        ) : view.failed ? (
          <p className={styles.exceptionEmpty}>任务详情读取失败，无法显示原始日志。</p>
        ) : (
          <p className={styles.exceptionEmpty}>该任务没有记录原始日志 message；可在任务详情中核对完整日志。</p>
        )}
      </div>
    </section>
  );
}
