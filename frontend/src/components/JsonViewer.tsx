import { useState, type ReactNode } from 'react';

type JsonViewerProps = {
  value: unknown;
  depth?: number;
  name?: string;
  defaultExpandDepth?: number;
};

const NEUTRAL = 0;

function isPrimitive(value: unknown): boolean {
  return value === null || typeof value !== 'object';
}

function primitive(value: unknown): ReactNode {
  if (value === null) return <span className="json-null">null</span>;
  if (value === undefined) return <span className="json-null">undefined</span>;
  if (typeof value === 'string') return <span className="json-string">&quot;{value}&quot;</span>;
  if (typeof value === 'number') return <span className="json-number">{String(value)}</span>;
  if (typeof value === 'boolean') return <span className="json-boolean">{String(value)}</span>;
  return <span>{String(value)}</span>;
}

function Node({ value, depth, name, defaultExpandDepth }: Required<JsonViewerProps>) {
  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as [string, unknown])
    : Object.entries(value as Record<string, unknown>);
  const [open, setOpen] = useState(depth <= defaultExpandDepth);
  const bracket = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
  if (!entries.length) {
    return <div className="json-line" style={{ paddingLeft: depth * 12 }}><span className="json-key">{name}</span>: {bracket[0]}{bracket[1]}</div>;
  }
  if (depth > 8) {
    return <div className="json-line" style={{ paddingLeft: depth * 12 }}><span className="json-key">{name}</span>: <span className="json-null">…（层级过深）</span></div>;
  }
  return (
    <div className="json-node">
      <button type="button" className="json-toggle" style={{ paddingLeft: depth * 12 }} onClick={() => setOpen((current) => !current)}>
        <span className="json-caret">{open ? '▾' : '▸'}</span>
        {name ? <><span className="json-key">{name}</span>: </> : null}
        <span className="json-bracket">{bracket[0]}</span>
        {!open && <span className="json-collapsed"> {entries.length} 项 {bracket[1]}</span>}
      </button>
      {open && (
        <div>
          {entries.map(([key, item]) => (
            isPrimitive(item)
              ? <div key={key} className="json-line" style={{ paddingLeft: (depth + 1) * 12 }}><span className="json-key">{key}</span>: {primitive(item)}</div>
              : <Node key={key} value={item} depth={depth + 1} name={key} defaultExpandDepth={defaultExpandDepth} />
          ))}
          <div className="json-line" style={{ paddingLeft: depth * 12 }}><span className="json-bracket">{bracket[1]}</span></div>
        </div>
      )}
    </div>
  );
}

/** 折叠 JSON 查看器：默认展开前两层，长内容按节点折叠。 */
export function JsonViewer({ value, depth = NEUTRAL, name = '', defaultExpandDepth = 2 }: JsonViewerProps) {
  if (value === null || value === undefined) {
    return <div className="json-empty">无数据</div>;
  }
  if (isPrimitive(value)) {
    return <pre className="json-primitive">{typeof value === 'string' ? value : String(value)}</pre>;
  }
  return (
    <div className="json-viewer">
      <Node value={value} depth={depth} name={name} defaultExpandDepth={defaultExpandDepth} />
    </div>
  );
}
