import { useState } from 'react';
import { App, Button } from 'antd';
import { CopyOutlined, GitlabOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { TaskDetailResponse } from '../../lib/types';
import { stepSummary, type TaskView } from './taskVerdict';
import { Section as TaskSection, SectionEmpty, ToneTag } from '../../components/section/Section';
import styles from './TaskSections.module.css';

/** Git 交付分区：分支里有什么、提交到哪里、能否核验；不自动合并必须写清楚。 */
export function DeliverySection({ detail, view, onOpenStep }: {
  detail: TaskDetailResponse;
  view: TaskView;
  onOpenStep: (name: string) => void;
}) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { delivery, changes } = view;
  const [copied, setCopied] = useState('');

  const copy = async (value: string, label: string) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      message.success(`${label}已复制`);
      window.setTimeout(() => setCopied(''), 2000);
    } catch {
      message.warning(`复制失败，请手动选择${label}`);
    }
  };

  const tag = delivery.landed
    ? { tone: 'ok' as const, text: delivery.finalApprovalPending ? '已交付 · 待人工复核' : '已正式交付' }
    : delivery.prepared ? { tone: 'warn' as const, text: '本地候选已准备' }
      : delivery.step?.status === 'failed' ? { tone: 'bad' as const, text: '交付失败' }
        : { tone: 'muted' as const, text: '未交付' };

  return (
    <TaskSection
      title="Git 分支交付"
      subtitle="分支里有什么、提交到哪里、能否核验"
      extra={<ToneTag tone={tag.tone}>{tag.text}</ToneTag>}
    >
      {delivery.step || delivery.branch || delivery.commit ? (
        <>
          <div className={styles.branchRow}>
            <span className={styles.branchIcon}><GitlabOutlined /></span>
            <div className={styles.branchCopy}>
              <label>{delivery.analysisOnly ? '分析报告分支' : '修复分支'}</label>
              <code>{delivery.branch || '分支名未记录'}</code>
            </div>
          </div>
          <div className={styles.kvPad}>
            <KvRow label="Commit SHA" value={delivery.commit || '未记录'} mono />
            <KvRow
              label="交付位置"
              value={delivery.pushed ? (delivery.finalApprovalPending ? '已推送远端，等待人工复核' : '已推送远端')
                : delivery.prepared ? '隔离工作区本地候选，尚未正式交付'
                  : delivery.localBranch ? '已写入本地仓库' : '未确认推送或落地'}
            />
            <KvRow
              label="提交内容"
              value={delivery.analysisOnly
                ? (delivery.reportPaths.join('、') || 'final.json 分析报告')
                : changes.count ? `${changes.count} 个源码文件` : '未记录源码文件清单'}
            />
          </div>
          <div className={styles.footRow}>
            {delivery.branch && (
              <Button size="small" icon={<CopyOutlined />} onClick={() => void copy(delivery.branch, '分支名')}>
                {copied === '分支名' ? '已复制' : '复制分支名'}
              </Button>
            )}
            {delivery.commit && (
              <Button size="small" icon={<CopyOutlined />} onClick={() => void copy(delivery.commit, 'Commit SHA')}>
                {copied === 'Commit SHA' ? '已复制' : '复制 Commit'}
              </Button>
            )}
            {delivery.finalApprovalPending && (
              <Button
                size="small"
                type="primary"
                onClick={() => navigate(`/approvals?task=${encodeURIComponent(detail.task.task_id)}`)}
              >
                去审批
              </Button>
            )}
            {delivery.step && <Button size="small" type="link" onClick={() => onOpenStep(delivery.step!.name)}>交付步骤记录</Button>}
          </div>
          <div className={`${styles.note} ${delivery.landed ? styles.noteOk : delivery.step?.status === 'failed' ? styles.noteMuted : styles.noteWarn}`}>
            <InfoCircleOutlined aria-hidden="true" />
            <span>{delivery.landed
              ? '系统没有自动合并。请在 Git 上核对变更并由你手动合并。'
              : delivery.prepared
                ? '当前仅准备本地候选；随后会正式交付此提交，再进入人工复核。'
                : delivery.step?.status === 'failed'
                  ? stepSummary(delivery.step) || '交付步骤失败，请查看节点输入、错误和日志。'
                  : '没有足够的提交证据证明分支已可靠落地。'}</span>
          </div>
        </>
      ) : (
        <SectionEmpty title="尚未进入分支交付" body="当前任务没有可核验的修复分支或报告分支提交。" />
      )}
    </TaskSection>
  );
}

function KvRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className={styles.kvRow}>
      <b>{label}</b>
      <span className={mono ? styles.monoValue : undefined} title={value}>{value}</span>
    </div>
  );
}
