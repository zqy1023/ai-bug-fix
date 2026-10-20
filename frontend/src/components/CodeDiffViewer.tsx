import { useMemo, useRef, useState, type RefObject } from 'react';
import { Empty, Tag } from 'antd';
import { FileCodeOutlined } from '@ant-design/icons';

type Dict = Record<string, unknown>;
type ChangeKind = 'same' | 'add' | 'remove';
type DiffRow = {
  key: string;
  kind: ChangeKind;
  leftNumber?: number;
  leftText?: string;
  rightNumber?: number;
  rightText?: string;
};
type SourceFile = {
  path: string;
  status: string;
  available: boolean;
  reason: string;
  before: string;
  after: string;
};

function object(value: unknown): Dict {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Dict
    : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function splitSourceLines(value: string): string[] {
  if (!value) return [];
  return value.replace(/\r\n/g, '\n').split('\n');
}

function statusText(status: string): string {
  switch (status.toLowerCase()) {
    case 'added': return '新增';
    case 'deleted': return '删除';
    case 'renamed': return '重命名';
    default: return '修改';
  }
}

function sourceFilesFromPayload(payload: unknown): SourceFile[] {
  const parsed = object(payload);
  return (Array.isArray(parsed.files) ? parsed.files : [])
    .map((entry) => object(entry))
    .filter((entry) => typeof entry.path === 'string' && entry.path.trim())
    .map((entry) => ({
      path: asText(entry.path),
      status: asText(entry.status) || 'modified',
      available: entry.available === true,
      reason: asText(entry.reason),
      before: asText(entry.before),
      after: asText(entry.after),
    }));
}

/** Legacy fallback:旧产物只有增删行时仍能展示差异，但会明示其没有上下文原文。 */
function fallbackFiles(diffText: string): SourceFile[] {
  if (!diffText.trim()) return [];
  const blocks: Array<{ path: string; before: string[]; after: string[]; status: string }> = [];
  let current: typeof blocks[number] | undefined;

  for (const line of diffText.split(/\r?\n/)) {
    if (line.startsWith('--- a/')) {
      if (current) blocks.push(current);
      current = {
        path: line.slice('--- a/'.length).trim(),
        before: [],
        after: [],
        status: 'modified',
      };
      continue;
    }
    if (!current) continue;
    if (line.startsWith('+++ b/')) {
      current.path = line.slice('+++ b/'.length).trim() || current.path;
      continue;
    }
    if (line.startsWith('@@')) {
      const status = line.match(/^@@\s+(added|deleted|modified|renamed)/i)?.[1]?.toLowerCase();
      if (status) current.status = status;
      continue;
    }
    if (line.startsWith('+')) current.after.push(line.slice(1));
    else if (line.startsWith('-')) current.before.push(line.slice(1));
    else if (line.startsWith(' ')) {
      // Preserve context lines in historical unified-diff artifacts that do not contain
      // whole-file snapshots, so the two panes show nearby source instead of only +/- lines.
      const context = line.slice(1);
      current.before.push(context);
      current.after.push(context);
    }
  }
  if (current) blocks.push(current);

  return blocks.filter((block) => block.path).map((block) => ({
    path: block.path,
    status: block.status,
    available: false,
    reason: '该历史产物只保存了增删行，未保存完整源码；以下是可用的差异片段。',
    before: block.before.join('\n'),
    after: block.after.join('\n'),
  }));
}

type Edit = { kind: ChangeKind; oldIndex?: number; newIndex?: number };

function myersEdits(oldLines: string[], newLines: string[]): Edit[] {
  const n = oldLines.length;
  const m = newLines.length;
  if (n === 0) return newLines.map((_, index) => ({ kind: 'add', newIndex: index }));
  if (m === 0) return oldLines.map((_, index) => ({ kind: 'remove', oldIndex: index }));
  if (oldLines.length === newLines.length && oldLines.every((line, index) => line === newLines[index])) {
    return oldLines.map((_, index) => ({ kind: 'same', oldIndex: index, newIndex: index }));
  }

  // Very large rewrites use common prefix/suffix anchors to cap browser memory.
  if (n * m > 1_000_000) {
    let prefix = 0;
    while (prefix < n && prefix < m && oldLines[prefix] === newLines[prefix]) prefix++;
    let suffix = 0;
    while (
      suffix < n - prefix && suffix < m - prefix
      && oldLines[n - suffix - 1] === newLines[m - suffix - 1]
    ) suffix++;
    const edits: Edit[] = [];
    for (let i = 0; i < prefix; i++) edits.push({ kind: 'same', oldIndex: i, newIndex: i });
    for (let i = prefix; i < n - suffix; i++) edits.push({ kind: 'remove', oldIndex: i });
    for (let i = prefix; i < m - suffix; i++) edits.push({ kind: 'add', newIndex: i });
    for (let i = suffix; i > 0; i--) {
      edits.push({ kind: 'same', oldIndex: n - i, newIndex: m - i });
    }
    return edits;
  }

  const maximum = n + m;
  const frontier = new Map<number, number>([[1, 0]]);
  const trace: Array<Map<number, number>> = [];
  let distance = 0;
  let complete = false;

  for (let d = 0; d <= maximum; d++) {
    trace.push(new Map(frontier));
    for (let k = -d; k <= d; k += 2) {
      const left = frontier.get(k - 1) ?? Number.NEGATIVE_INFINITY;
      const down = frontier.get(k + 1) ?? Number.NEGATIVE_INFINITY;
      let x = k === -d || (k !== d && left < down)
        ? (frontier.get(k + 1) ?? 0)
        : (frontier.get(k - 1) ?? 0) + 1;
      let y = x - k;
      while (x < n && y < m && oldLines[x] === newLines[y]) { x++; y++; }
      frontier.set(k, x);
      if (x >= n && y >= m) {
        distance = d;
        complete = true;
        break;
      }
    }
    if (complete) break;
  }

  const reversed: Edit[] = [];
  let x = n;
  let y = m;
  for (let d = distance; d >= 0; d--) {
    const snapshot = trace[d];
    const k = x - y;
    const left = snapshot.get(k - 1) ?? Number.NEGATIVE_INFINITY;
    const down = snapshot.get(k + 1) ?? Number.NEGATIVE_INFINITY;
    const previousK = k === -d || (k !== d && left < down) ? k + 1 : k - 1;
    const previousX = snapshot.get(previousK) ?? 0;
    const previousY = previousX - previousK;

    while (x > previousX && y > previousY) {
      reversed.push({ kind: 'same', oldIndex: x - 1, newIndex: y - 1 });
      x--;
      y--;
    }
    if (d === 0) break;
    if (x === previousX && y > 0) {
      reversed.push({ kind: 'add', newIndex: y - 1 });
      y--;
    } else if (x > 0) {
      reversed.push({ kind: 'remove', oldIndex: x - 1 });
      x--;
    }
  }
  return reversed.reverse();
}

function alignEdits(edits: Edit[], oldLines: string[], newLines: string[]): DiffRow[] {
  const rows: DiffRow[] = [];
  let pendingDeletes: Edit[] = [];
  let pendingAdds: Edit[] = [];
  let seq = 0;

  const flush = () => {
    const count = Math.max(pendingDeletes.length, pendingAdds.length);
    for (let index = 0; index < count; index++) {
      const deleted = pendingDeletes[index];
      const added = pendingAdds[index];
      rows.push({
        key: `change-${seq++}`,
        kind: deleted && added ? 'add' : deleted ? 'remove' : 'add',
        leftNumber: deleted?.oldIndex === undefined ? undefined : deleted.oldIndex + 1,
        leftText: deleted?.oldIndex === undefined ? undefined : oldLines[deleted.oldIndex],
        rightNumber: added?.newIndex === undefined ? undefined : added.newIndex + 1,
        rightText: added?.newIndex === undefined ? undefined : newLines[added.newIndex],
      });
    }
    pendingDeletes = [];
    pendingAdds = [];
  };

  for (const edit of edits) {
    if (edit.kind === 'remove') { pendingDeletes.push(edit); continue; }
    if (edit.kind === 'add') { pendingAdds.push(edit); continue; }
    flush();
    rows.push({
      key: `same-${seq++}`,
      kind: 'same',
      leftNumber: edit.oldIndex === undefined ? undefined : edit.oldIndex + 1,
      leftText: edit.oldIndex === undefined ? '' : oldLines[edit.oldIndex],
      rightNumber: edit.newIndex === undefined ? undefined : edit.newIndex + 1,
      rightText: edit.newIndex === undefined ? '' : newLines[edit.newIndex],
    });
  }
  flush();
  return rows;
}

function rowsFor(file: SourceFile): DiffRow[] {
  const oldLines = splitSourceLines(file.before);
  const newLines = splitSourceLines(file.after);
  return alignEdits(myersEdits(oldLines, newLines), oldLines, newLines);
}

function CodePane({
  side, rows, scrollRef, onScroll,
}: {
  side: 'old' | 'new';
  rows: DiffRow[];
  scrollRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}) {
  return (
    <div className={`code-diff-pane is-${side}`}>
      <div className="code-diff-pane-head">
        <span>{side === 'old' ? '原始版本' : '修改后'}</span>
        <span>{rows.length ? `${rows.length} 行对照` : '空文件'}</span>
      </div>
      <div className="code-diff-scroll" ref={scrollRef} onScroll={onScroll}>
        {rows.map((row) => {
          const lineNumber = side === 'old' ? row.leftNumber : row.rightNumber;
          const lineText = side === 'old' ? row.leftText : row.rightText;
          const cellKind = row.kind === 'same' ? 'same'
            : side === 'old' ? (row.leftNumber === undefined ? 'blank' : 'remove')
              : (row.rightNumber === undefined ? 'blank' : 'add');
          return (
            <div key={row.key} className={`code-diff-line is-${cellKind}`}>
              <span className="code-diff-gutter">{lineNumber ?? ''}</span>
              <code>{lineText === '' || lineText === undefined ? ' ' : lineText}</code>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Shared editor-like diff: file tree + old/new panes, line numbers, context, and highlighted changes. */
export function CodeDiffViewer({
  payload,
  selectedPath,
  onSelectedPathChange,
}: {
  payload: unknown;
  selectedPath?: string;
  onSelectedPathChange?: (path: string) => void;
}) {
  const [internalPath, setInternalPath] = useState('');
  const record = object(payload);
  const fullText = typeof payload === 'string' ? payload : asText(record.diff);
  const diffWarnings = useMemo(
    () => fullText.split(/\\r?\\n/)
      .filter((line) => line.startsWith('# 单文件差异已截断') || line.startsWith('# 另有 ')),
    [fullText],
  );
  const primary = useMemo(() => sourceFilesFromPayload(payload), [payload]);
  const files = useMemo(() => {
    const fragments = new Map(fallbackFiles(fullText).map((file) => [file.path, file] as const));
    if (primary.length) {
      // A file can be listed in the side-by-side payload but omitted from full source
      // because of a size/budget limit. Keep that file selectable and show its diff
      // fragment instead of rendering two blank panes.
      return primary.map((file) => {
        if (file.available) return file;
        const fallback = fragments.get(file.path);
        return fallback
          ? { ...file, before: fallback.before, after: fallback.after, reason: file.reason || fallback.reason }
          : file;
      });
    }
    return [...fragments.values()];
  }, [primary, fullText]);

  const fileRows = useMemo(
    () => new Map(files.map((file) => [file.path, rowsFor(file)] as const)),
    [files],
  );
  const activePath = files.some((file) => file.path === selectedPath)
    ? selectedPath!
    : files.some((file) => file.path === internalPath)
      ? internalPath
      : files[0]?.path ?? '';
  const activeFile = files.find((file) => file.path === activePath);
  const activeRows = activeFile ? fileRows.get(activeFile.path) ?? [] : [];
  const oldScrollRef = useRef<HTMLDivElement>(null);
  const newScrollRef = useRef<HTMLDivElement>(null);
  const syncOldToNew = () => {
    const oldPane = oldScrollRef.current;
    const newPane = newScrollRef.current;
    if (oldPane && newPane && newPane.scrollTop !== oldPane.scrollTop) newPane.scrollTop = oldPane.scrollTop;
  };
  const syncNewToOld = () => {
    const oldPane = oldScrollRef.current;
    const newPane = newScrollRef.current;
    if (oldPane && newPane && oldPane.scrollTop !== newPane.scrollTop) oldPane.scrollTop = newPane.scrollTop;
  };

  const select = (path: string) => {
    setInternalPath(path);
    onSelectedPathChange?.(path);
  };

  if (!files.length) {
    return <Empty description="当前产物没有可显示的源码差异。" />;
  }

  return (
    <div className="code-diff-workbench">
      {diffWarnings.length > 0 && (
        <div className="code-diff-truncation-warning" role="status">
          {diffWarnings.map((warning) => warning.replace(/^#\\s*/, '')).join('；')}
        </div>
      )}
      <aside className="code-diff-file-tree">
        <header><span>变更文件</span><Tag>{files.length}</Tag></header>
        {files.map((file) => {
          const active = file.path === activePath;
          const rows = fileRows.get(file.path) ?? [];
          const added = rows.filter((row) => row.rightNumber !== undefined && row.kind !== 'same').length;
          const removed = rows.filter((row) => row.leftNumber !== undefined && row.kind !== 'same').length;
          return (
            <button
              type="button"
              key={file.path}
              className={`code-diff-file-item${active ? ' is-active' : ''}`}
              onClick={() => select(file.path)}
              title={file.path}
            >
              <span className="code-diff-file-name"><FileCodeOutlined />{file.path}</span>
              <span className="code-diff-file-meta">
                <span className="code-diff-added">+{added}</span>
                <span className="code-diff-removed">−{removed}</span>
                <span>{statusText(file.status)}</span>
              </span>
            </button>
          );
        })}
      </aside>
      <section className="code-diff-editor">
        {activeFile ? (
          <>
            <header className="code-diff-file-banner">
              <div>
                <strong>{activeFile.path}</strong>
                <span>{activeFile.available ? '显示完整文件上下文' : activeFile.reason}</span>
              </div>
              <Tag color={activeFile.status === 'added' ? 'success' : activeFile.status === 'deleted' ? 'error' : 'blue'}>
                {statusText(activeFile.status)}
              </Tag>
            </header>
            <div className="code-diff-two-pane">
              {activeFile.available ? (
                <div className="code-diff-panes">
                  <CodePane side="old" rows={activeRows} scrollRef={oldScrollRef} onScroll={syncOldToNew} />
                  <CodePane side="new" rows={activeRows} scrollRef={newScrollRef} onScroll={syncNewToOld} />
                </div>
              ) : (
                <div className="code-diff-legacy">
                  <div className="code-diff-panes">
                    <CodePane side="old" rows={activeRows} scrollRef={oldScrollRef} onScroll={syncOldToNew} />
                    <CodePane side="new" rows={activeRows} scrollRef={newScrollRef} onScroll={syncNewToOld} />
                  </div>
                  <p>完整源码不可用，以下仅显示已保存的差异片段；原因见上方提示。</p>
                </div>
              )}
            </div>
          </>
        ) : <Empty description="请选择一个文件。" />}
      </section>
    </div>
  );
}
