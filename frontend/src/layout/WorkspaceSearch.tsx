import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Empty, Input, Modal, Spin } from 'antd';
import { SearchOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import styles from './WorkspaceSearch.module.css';

export function WorkspaceSearch() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        const anotherDialog = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).some((element) => element.getClientRects().length > 0);
        if (!open && anotherDialog) return;
        setOpen((value) => !value);
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [open]);
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(draft.trim()), 200);
    return () => clearTimeout(timer);
  }, [draft]);
  const results = useQuery({
    queryKey: queryKeys.runs({page:1,keyword}),
    queryFn: () => api.runs({page:1,page_size:12,keyword}),
    enabled: open && Boolean(keyword),
  });
  const visit = (path: string) => { setOpen(false); navigate(path); };
  return <>
    <button type="button" className={styles.trigger} aria-label="快捷检索" onClick={() => setOpen(true)}>
      <SearchOutlined /><span>快捷检索…</span><kbd>Ctrl+K</kbd>
    </button>
    <Modal title="快捷检索" open={open} onCancel={() => setOpen(false)} footer={null} width={560} destroyOnHidden>
      <Input autoFocus prefix={<SearchOutlined />} aria-label="快捷搜索批次 ID" placeholder="输入批次 ID，或选择下方页面" value={draft} onChange={(event) => setDraft(event.target.value)} onPressEnter={() => { const first = results.data?.items[0]; if (keyword && draft.trim() === keyword && first) visit(`/runs/${encodeURIComponent(first.run_id)}`); else setKeyword(draft.trim()); }} />
      <div className={styles.results}>
        {!keyword ? <><span className={styles.label}>跳转页面</span><button onClick={() => visit('/runs')}>执行批次<ArrowRightOutlined /></button><button onClick={() => visit('/approvals')}>交付审批<ArrowRightOutlined /></button></> : results.isLoading ? <div className={styles.loading}><Spin size="small" /> 正在查找批次…</div> : results.isError ? <p className={styles.loading}>检索暂时不可用，请稍后重试。</p> : results.data?.items.length ? <><span className={styles.label}>匹配的批次</span>{results.data.items.map((run) => <button key={run.run_id} onClick={() => visit(`/runs/${encodeURIComponent(run.run_id)}`)}><code>{run.run_id}</code><ArrowRightOutlined /></button>)}</> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的批次" />}
      </div>
    </Modal>
  </>;
}
