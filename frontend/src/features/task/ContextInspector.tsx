import type { StepContext } from '../../lib/types';

/**
 * 上下文（三级信息，默认折叠）：变更文件、方案范围、交付状态、输出计数与标志位。
 * 步骤/状态/耗时等已在上方的节点轨与步骤头展示，这里不重复。
 */
export function ContextInspector({ context }: { context?: StepContext }) {
  const files = context?.changed_files ?? [];
  const counts = context ? Object.entries(context.counts || {}) : [];
  const flags = context ? Object.entries(context.flags || {}) : [];
  const hasContent = files.length > 0
    || counts.length > 0
    || flags.length > 0
    || Boolean(context?.change_plan_scope)
    || Boolean(context?.delivery_status);
  if (!context || !hasContent) return null;

  return (
    <details className="context-panel">
      <summary>
        上下文 · 变更文件 {files.length}
        {context.change_plan_scope && ` · ${context.change_plan_scope}`}
        {context.delivery_status && ` · ${context.delivery_status}`}
      </summary>

      {files.length > 0 && (
        <ul className="inspector-files">
          {files.map((file) => <li key={file}><code>{file}</code></li>)}
        </ul>
      )}
      {counts.length > 0 && (
        <div className="inspector-chips">
          {counts.map(([key, value]) => (
            <span key={key}>{key} {value}</span>
          ))}
        </div>
      )}
      {flags.length > 0 && (
        <div className="inspector-chips">
          {flags.map(([key, value]) => (
            <span key={key} className={value ? 'is-on' : ''}>{key}={String(value)}</span>
          ))}
        </div>
      )}
    </details>
  );
}
