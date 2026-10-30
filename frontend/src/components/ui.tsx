import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import { Link } from 'react-router-dom';
import { taskStatus, runStatus } from '../lib/status';
import type { Tone } from '../lib/types';

/* ---------------------------------- 图标 ---------------------------------- */

const ICON_PATHS: Record<string, ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 16 9 5 9-5" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m16 16 4 4" /></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 7h18s-3 0-3-7" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></>,
  chevron: <path d="m9 18 6-6-6-6" />,
  down: <path d="m6 9 6 6 6-6" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" /></>,
  download: <><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  pause: <><path d="M9 5v14M15 5v14" /></>,
  stop: <rect x="6" y="6" width="12" height="12" rx="1.5" />,
  refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  code: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16" /></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>,
  arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
  external: <><path d="M14 3h7v7" /><path d="M10 14 21 3" /><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" /></>,
};

export type IconName = keyof typeof ICON_PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

/* ---------------------------------- 基础 ---------------------------------- */

export function PageHeader({
  eyebrow,
  title,
  meta,
  actions,
}: {
  eyebrow: string;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {meta ? <p className="lede">{meta}</p> : null}
      </div>
      {actions ? <div className="head-actions">{actions}</div> : null}
    </header>
  );
}

export function Badge({ tone = 'idle', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`}>
      <i />
      {children}
    </span>
  );
}

export function RunStatusBadge({ status }: { status?: string | null }) {
  const meta = runStatus(status);
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function TaskStatusBadge({ result, lifecycle }: { result?: string | null; lifecycle?: string | null }) {
  const meta = taskStatus(result, lifecycle);
  if (result === 'CHANGED_DELIVERED' || result === 'UNCHANGED_DELIVERED') {
    const changed = result === 'CHANGED_DELIVERED';
    return <span className={`badge ${meta.tone} delivery-status ${changed ? 'with-code' : 'report-only'}`}
      title={changed ? '包含已交付的源码修改' : '仅交付分析报告，没有源码修改'}>
      <Icon name={changed ? 'code' : 'file'} size={13} />{meta.label}
    </span>;
  }
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function Card({
  children,
  className = '',
  id,
}: {
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`.trim()} id={id}>
      {children}
    </section>
  );
}

export function CardHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="card-head">
      <div className="row">
        <div className="card-title">{title}</div>
        {sub ? <span className="card-sub">{sub}</span> : null}
      </div>
      {actions}
    </div>
  );
}

export function CardBody({ children, flush = false, tight = false }: { children: ReactNode; flush?: boolean; tight?: boolean }) {
  return <div className={`card-body${flush ? ' flush' : ''}${tight ? ' tight' : ''}`}>{children}</div>;
}

export function Verdict({
  tone = 'info',
  title,
  desc,
  icon = '!',
  actions,
}: {
  tone?: Tone;
  title: ReactNode;
  desc?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={`verdict ${tone}`}>
      <div className="v-icon">{icon}</div>
      <div className="v-body">
        <div className="v-title">{title}</div>
        {desc ? <p className="v-desc">{desc}</p> : null}
      </div>
      {actions ? <div className="v-actions">{actions}</div> : null}
    </section>
  );
}

export interface TabItem {
  key: string;
  label: string;
  href?: string;
  count?: number;
}

export function Tabs({ items, active, className = '' }: { items: TabItem[]; active: string; className?: string }) {
  return (
    <div className={`tabs ${className}`.trim()}>
      {items.map((item) => {
        const content = (
          <>
            {item.label}
            {item.count !== undefined ? <span className="n">{item.count}</span> : null}
          </>
        );
        if (item.href) {
          return (
            <Link key={item.key} className={`tab${item.key === active ? ' active' : ''}`} to={item.href}>
              {content}
            </Link>
          );
        }
        return (
          <span key={item.key} className={`tab${item.key === active ? ' active' : ''}`}>
            {content}
          </span>
        );
      })}
    </div>
  );
}

export function Kpi({ value, label, tone }: { value: ReactNode; label: ReactNode; tone?: 'ok' | 'info' | 'warn' | 'danger' }) {
  return (
    <div className="kpi">
      <div className={`kpi-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}

export function Empty({
  title,
  desc,
  icon = '·',
  action,
}: {
  title: ReactNode;
  desc?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {desc ? <p>{desc}</p> : null}
      {action}
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error || '未知错误');
  return (
    <div className="empty">
      <div className="empty-icon" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>!</div>
      <h3>加载失败</h3>
      <p>{message}</p>
      {retry ? <button className="btn" onClick={retry}><Icon name="refresh" />重试</button> : null}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="stack-8" aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton" style={{ width: `${88 - index * 9}%` }} />
      ))}
    </div>
  );
}

export function ProgressBar({ pct, tone }: { pct: number; tone?: 'ok' | 'warn' | 'danger' }) {
  return (
    <div className={`bar${tone ? ` ${tone}` : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.max(0, Math.min(100, pct)))} aria-label="完成进度">
      <i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

export function JsonBlock({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2);
  return (
    <pre className="raw" style={maxHeight ? { maxHeight } : undefined}>
      {text}
    </pre>
  );
}

export function RawMessage({ text, truncated }: { text: string; truncated?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const foldable = text.length > 1600 || text.split('\n').length > 10;
  return (
    <div>
      <pre id={id} className={`raw${foldable && !expanded ? ' raw-collapsed' : ''}`} style={{ maxHeight: foldable && !expanded ? 260 : 'none' }} tabIndex={0} aria-label="完整异常 message">
        {text}
      </pre>
      {foldable ? <button className="btn sm" type="button" style={{ marginTop: 10 }} aria-expanded={expanded} aria-controls={id}
        onClick={() => setExpanded((value) => !value)}>{expanded ? '收起原文' : `展开完整原文（${text.length.toLocaleString()} 字符）`}</button> : null}
      {truncated ? <p className="note" style={{ marginTop: 8 }}>原始记录不可用，当前为裁剪预览。</p> : null}
    </div>
  );
}

export function Button({
  variant = 'default',
  size,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'ghost' | 'danger'; size?: 'sm' }) {
  const classes = ['btn', variant !== 'default' ? variant : '', size === 'sm' ? 'sm' : ''].filter(Boolean).join(' ');
  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}

/* ---------------------------------- 弹层 ---------------------------------- */

export function Modal({
  open,
  onClose,
  title,
  desc,
  children,
  footer,
  width = 640,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  desc?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="modal" style={{ width }} role="dialog" aria-modal="true" aria-label={String(title)}>
        <div className="modal-head">
          <h2>{title}</h2>
          {desc ? <p>{desc}</p> : null}
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </>
  );
}

export function Drawer({
  open,
  onClose,
  title,
  desc,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  desc?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={String(title)}>
        <div className="drawer-head">
          <div>
            <div className="eyebrow">Detail</div>
            <h2>{title}</h2>
            {desc ? <p>{desc}</p> : null}
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="关闭">
            <Icon name="close" size={18} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </aside>
    </>
  );
}

/* ---------------------------------- 表单 ---------------------------------- */

export function Field({
  label,
  hint,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
      {hint ? <div className="field-hint">{hint}</div> : null}
    </div>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  const { mono, className = '', ...rest } = props;
  return <input className={`input${mono ? ' mono' : ''} ${className}`.trim()} {...rest} />;
}

/* ---------------------------------- Toast ---------------------------------- */

type ToastItem = { id: string; title: string; desc?: string; tone: 'ok' | 'danger' };

const ToastContext = createContext<{ push: (title: string, desc?: string, tone?: 'ok' | 'danger') => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((title: string, desc?: string, tone: 'ok' | 'danger' = 'ok') => {
    const id = Math.random().toString(36).slice(2);
    setItems((current) => [...current, { id, title, desc, tone }]);
    window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 3800);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div style={{ position: 'fixed', right: 24, bottom: 24, display: 'grid', gap: 8, zIndex: 90 }}>
        {items.map((item) => (
          <div className="toast" key={item.id}>
            <div className="t-icon" style={item.tone === 'danger' ? { background: 'var(--danger-soft)', color: 'var(--danger)' } : undefined}>
              {item.tone === 'danger' ? '!' : '✓'}
            </div>
            <div>
              <div className="t-title">{item.title}</div>
              {item.desc ? <div className="t-desc">{item.desc}</div> : null}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside ToastProvider');
  return value;
}

export function CopyButton({ text, label = '复制', size = 'sm' }: { text: string; label?: string; size?: 'sm' }) {
  const { push } = useToast();
  return (
    <Button
      size={size}
      onClick={() => {
        void navigator.clipboard
          .writeText(text)
          .then(() => push('已复制', '内容已写入剪贴板'))
          .catch(() => push('复制失败', '请手动选择文本', 'danger'));
      }}
    >
      <Icon name="copy" />
      {label}
    </Button>
  );
}

export function useDomId(prefix: string): string {
  const id = useId();
  return `${prefix}-${id.replace(/:/g, '')}`;
}
