import { useMemo, useState } from 'react';
import { filePatch } from '../lib/diff';
import type { FileChange } from '../lib/types';

export function FileDiff({ file }: { file: FileChange }) {
  const [mode, setMode] = useState<'patch' | 'before' | 'after'>('patch');
  const labels = { added: '新增', deleted: '删除', modified: '修改' };
  const patch = useMemo(() => filePatch(file), [file]);
  const added = patch.split('\n').filter(line => line.startsWith('+') && !line.startsWith('+++')).length;
  const deleted = patch.split('\n').filter(line => line.startsWith('-') && !line.startsWith('---')).length;
  let oldLine = 0, newLine = 0;
  const rows = patch.split('\n').map((line, index) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line);
    let oldNumber: number | undefined, newNumber: number | undefined;
    let kind = 'ctx';
    if (hunk) { oldLine = Number(hunk[1]); newLine = Number(hunk[2]); kind = 'hunk'; }
    else if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('\\')) kind = 'hunk';
    else if (line.startsWith('-')) { oldNumber = oldLine++; kind = 'del'; }
    else if (line.startsWith('+')) { newNumber = newLine++; kind = 'add'; }
    else if (line.startsWith(' ')) { oldNumber = oldLine++; newNumber = newLine++; }
    return <span className={`change-line ${kind}`} key={index}><span>{oldNumber}</span><span>{newNumber}</span><code>{line || ' '}</code></span>;
  });
  const content = mode === 'before' ? file.before : file.after;
  return <details className="file-change" open>
    <summary><span className="mono">{file.path}</span><span>{labels[file.status || 'modified']} · +{added} −{deleted}</span></summary>
    {!file.available ? <p className="note">{file.reason || '文件内容不可用'}</p> : <>
      <div className="change-toolbar" role="group" aria-label={`${file.path} 展示模式`}>
        {(['patch', 'before', 'after'] as const).map(value => <button type="button" className="btn" aria-pressed={mode === value} onClick={() => setMode(value)} key={value}>
          {{ patch: '差异', before: '修改前', after: '修改后' }[value]}</button>)}
      </div>
      <div className="change-code">{mode === 'patch' ? (patch ? rows : <p className="note">文件存在状态改变，文本内容为空。</p>) :
        file[mode === 'before' ? 'before_exists' : 'after_exists'] === false ? <p className="note">此版本不存在该文件。</p> :
        (content || '').split('\n').map((line, index) => <span className="change-line source" key={index}><span>{index + 1}</span><code>{line}</code></span>)}</div>
    </>}
  </details>;
}

