import { Collapse, Tag } from 'antd';
import type { PromptSnapshot } from '../../lib/types';
import { EmptyState } from '../../components/EmptyState';

function Section({ text }: { text?: string }) {
  if (!text) return <div className="prompt-empty">该段为空</div>;
  return <pre className="prompt-text">{text}</pre>;
}

export function PromptViewer({ snapshot }: { snapshot?: PromptSnapshot | Record<string, never> }) {
  const prompt = snapshot as PromptSnapshot | undefined;
  if (!prompt || !('system_prompt_common' in (prompt as object))) {
    return (
      <EmptyState
        title="本次调用没有提示词快照"
        description="旧运行或服务端关闭了提示词快照。提示词快照默认开启、脱敏并按上限截断。"
        compact
      />
    );
  }

  const items = [
    { key: 'common', label: `公共规则 · ${prompt.chars?.system_prompt_common ?? 0} 字`, children: <Section text={prompt.system_prompt_common} /> },
    { key: 'role', label: `角色提示词 · ${prompt.chars?.system_prompt_role ?? 0} 字`, children: <Section text={prompt.system_prompt_role} /> },
    { key: 'task', label: `任务上下文 · ${prompt.chars?.task_prompt ?? 0} 字`, children: <Section text={prompt.task_prompt} /> },
    { key: 'context', label: `上下文快照 · ${prompt.chars?.prompt_context ?? 0} 字`, children: <Section text={prompt.prompt_context} /> },
  ];

  return (
    <div className="prompt-viewer">
      <div className="prompt-meta">
        <Tag color="blue">{prompt.prompt_version || 'unknown'}</Tag>
        <span className="prompt-hash">hash {prompt.prompt_hash?.slice(0, 12) || '—'}</span>
        <span>attempt {prompt.attempt}</span>
        <span>{prompt.model}</span>
        {prompt.redacted && <span className="prompt-flag">已脱敏</span>}
        {prompt.truncated && <span className="prompt-flag is-warn">已截断</span>}
      </div>
      <Collapse defaultActiveKey={['common']} ghost items={items} />
    </div>
  );
}
