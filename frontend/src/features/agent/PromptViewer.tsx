import { Collapse } from 'antd';
import type { PromptSnapshot } from '../../lib/types';
import { EmptyState } from '../../components/EmptyState';
import styles from './PromptViewer.module.css';

function Section({ text }: { text?: string }) {
  if (!text) return <div className={styles.empty}>该段为空</div>;
  return <pre className={styles.text}>{text}</pre>;
}

export function PromptViewer({ snapshot }: { snapshot?: PromptSnapshot | Record<string, never> }) {
  const prompt = snapshot as PromptSnapshot | undefined;
  if (!prompt || !('system_prompt_common' in (prompt as object))) {
    return (
      <EmptyState
        title="本次调用没有提示词快照"
        description="旧运行或服务端关闭了提示词快照。新运行完整保留实际提示词。"
        compact
      />
    );
  }

  const items = [
    { key: 'common', label: `公共规则 · ${prompt.chars?.system_prompt_common ?? 0} 字`, children: <Section text={prompt.system_prompt_common} /> },
    { key: 'role', label: `角色提示词 · ${prompt.chars?.system_prompt_role ?? 0} 字`, children: <Section text={prompt.system_prompt_role} /> },
    { key: 'task', label: `任务上下文 · ${prompt.chars?.task_prompt ?? 0} 字`, children: <Section text={prompt.task_prompt} /> },
    { key: 'context', label: `上下文快照 · ${prompt.chars?.prompt_context ?? 0} 字`, children: <Section text={prompt.prompt_context} /> },
    ...(prompt.full_task_context ? [{ key: 'full', label: `完整任务证据（压缩前归档） · ${prompt.chars?.full_task_context ?? prompt.full_task_context.length} 字`, children: <Section text={prompt.full_task_context} /> }] : []),
  ];

  return (
    <div className={styles.viewer}>
      <div className={styles.meta}>
        <span className={styles.chip}>{prompt.prompt_version || '未记录版本'}</span>
        <span className={styles.hash}>哈希 {prompt.prompt_hash?.slice(0, 12) || '未记录'}</span>
        <span>第 {prompt.attempt} 次尝试</span>
        <span>{prompt.model}</span>
        {prompt.redacted && <span className={styles.flag}>已脱敏</span>}
        {prompt.truncated && <span className={`${styles.flag} ${styles.flagWarn}`}>已截断</span>}
      </div>
      <Collapse defaultActiveKey={['task']} ghost items={items} />
    </div>
  );
}
