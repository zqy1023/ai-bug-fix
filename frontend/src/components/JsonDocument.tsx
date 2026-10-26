import { useMemo, useState } from 'react';
import { App, Button, Input, Radio } from 'antd';
import { CopyOutlined, DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import { JsonViewer } from './JsonViewer';
import styles from './JsonDocument.module.css';

export interface JsonDocumentProps { readonly value: unknown; readonly filename: string }
export function JsonDocument({ value, filename }: JsonDocumentProps) {
  const { message } = App.useApp();
  const [search, setSearch] = useState('');
  const [mode, setMode] = useState('tree');
  const json = useMemo(() => JSON.stringify(value ?? {}, null, 2), [value]);
  const matching = useMemo(() => json.split('\n').map((line, index) => ({ line, index })).filter(({line}) => line.toLowerCase().includes(search.toLowerCase())), [json, search]);
  const copy = async () => { try { await navigator.clipboard.writeText(json); void message.success('JSON 已复制'); } catch { void message.error('复制失败，请切换原文后手动复制'); } };
  const download = () => {
    const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
  };
  return <section className={styles.root} aria-label="结构化证据查看器">
    <div className={styles.toolbar}>
      <Input aria-label="查找 JSON 内容" prefix={<SearchOutlined />} placeholder="查找键或值…" allowClear value={search} onChange={e => setSearch(e.target.value)} />
      <Radio.Group aria-label="JSON 展示方式" value={mode} onChange={e => setMode(e.target.value)} optionType="button" options={[{value:'tree',label:'结构'},{value:'raw',label:'原文'}]} />
      <Button aria-label="复制 JSON" icon={<CopyOutlined />} onClick={() => void copy()}>复制</Button><Button aria-label="下载 JSON" icon={<DownloadOutlined />} onClick={download}>下载</Button>
    </div>
    <div className={styles.meta}><code>{filename}</code><span>{search ? `${matching.length} 行匹配` : `${new TextEncoder().encode(json).length.toLocaleString()} 字节`}</span></div>
    <div className={styles.body}>{search ? matching.length ? <pre>{matching.map(({line,index}) => <div key={index}><span className={styles.line}>{index + 1}</span>{line}</div>)}</pre> : <p className={styles.empty}>没有匹配的键或值</p> : mode === 'raw' ? <pre>{json}</pre> : <JsonViewer value={value ?? {}} defaultExpandDepth={2} />}</div>
  </section>;
}
