import type { StepContext } from '../../lib/types';

/**
 * 上下文（三级信息，默认折叠）：变更文件、方案范围、交付状态、输出计数与标志位。
 * 步骤/状态/耗时等已在上方的节点轨与步骤头展示，这里不重复。
 */
const COUNT_LABELS: Record<string, string> = {
  changed_files: '变更文件',
  files: '文件',
  checks: '检查项',
  failures: '失败项',
  commands: '命令',
  candidates: '候选文件',
  skipped_large_files: '超限文件',
  source_evidence: '源码证据',
  facts: '定位依据',
  evidence_gaps: '证据缺口',
  secret_samples: '敏感信息命中',
  dangerous_calls: '危险调用',
  required_changes: '待处理项',
};
const FLAG_LABELS: Record<string, string> = {
  legacy_only: '仅旧框架',
  legacy_dependency: '依赖旧框架',
  scope_escalation_required: '需要扩大修改范围',
  approval_pending: '等待审批',
};

function scopeLabel(value: string): string {
  if (value === 'carrier') return '单一承运商';
  if (value === 'project') return '项目 / 共享层';
  return value;
}

function deliveryLabel(value: string): string {
  return ({
    ANALYSIS_ONLY: '仅分析报告',
    REPAIR: '源码修复',
    SUCCESS: '已交付',
    FAILED: '交付失败',
    SKIPPED: '未交付',
  } as Record<string, string>)[value] || value;
}

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
        {context.change_plan_scope && ` · ${scopeLabel(context.change_plan_scope)}`}
        {context.delivery_status && ` · ${deliveryLabel(context.delivery_status)}`}
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
