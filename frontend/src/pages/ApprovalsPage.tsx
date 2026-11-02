import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type FinalApproval } from '../lib/api';
import { Card, CardBody, ErrorBox, Skeleton } from '../components/ui';

export function ApprovalsPage() {
  const [status, setStatus] = useState('pending');
  const [selected, setSelected] = useState<FinalApproval | null>(null);
  const [reasonCode, setReasonCode] = useState('business');
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState('unified_fixer_agent');
  const client = useQueryClient();
  const list = useQuery({queryKey: ['approvals', status], queryFn: () => api.approvals(status), refetchInterval: 15000});
  const mutation = useMutation({mutationFn: (decision: 'approve' | 'reject' | 'close') => {
    if (!selected) throw new Error('请选择待审批候选');
    return api.decideApproval(selected, decision, reason, reasonCode, target);
  }, onSuccess: () => {setSelected(null); setReason(''); void client.invalidateQueries({queryKey: ['approvals']});}});
  return <><h1>最终审批</h1><p>核对原始异常、独立审查和实际差异后决定。批准后仍由人工合并，审批不会阻塞其他任务。</p>
    <label>审批状态 <select value={status} onChange={e => {setStatus(e.target.value); setSelected(null);}}>
      <option value="pending">待审批</option><option value="approved">已批准</option>
      <option value="rejected">已驳回</option><option value="closed">已关闭</option>
    </select></label>
    <Card><CardBody>{list.isLoading ? <Skeleton rows={3}/> : list.error ? <ErrorBox error={list.error}/> :
      list.data?.items.length ? list.data.items.map(item => <div key={item.approval_id} style={{padding: 12, borderBottom: '1px solid #ddd'}}>
        <strong>{item.task_id.split('__')[0]}</strong> · {item.branch} · <code>{item.commit.slice(0, 12)}</code>{' '}
        <Link to={item.detail_url} target="_blank" rel="noreferrer">查看原始问题、审查及代码差异</Link>{' '}
        {item.status === 'pending' && !item.read_only ? <button className="btn" onClick={() => setSelected(item)}>处理候选</button> : null}
        {item.reason ? <p>{item.reason}</p> : null}
        {!item.read_only && item.status === 'rejected' && ['pending', 'starting'].includes(item.retry_status ?? '') ? <button className="btn" onClick={() => {
          void api.retryApproval(item).then(() => client.invalidateQueries({queryKey: ['approvals']})).catch(error => window.alert(String(error)));
        }}>恢复驳回任务</button> : null}
      </div>) : <p>当前没有该状态的审批记录。</p>}</CardBody></Card>
    {selected ? <Card><CardBody><h2>处理 {selected.task_id.split('__')[0]}</h2><p>候选提交：{selected.commit}</p>
      <label>原因类别 <select value={reasonCode} onChange={e => setReasonCode(e.target.value)}>
        <option value="business">业务语义</option><option value="scope">修改范围</option><option value="regression">回归问题</option>
        <option value="evidence">证据不足</option><option value="other">其他</option>
      </select></label>{' '}
      <label>驳回节点 <select value={target} onChange={e => setTarget(e.target.value)}>
        <option value="unified_fixer_agent">重新分析修复</option><option value="review_agent">重新审查</option><option value="generate_report">重新生成交付</option>
      </select></label><p><textarea aria-label="处理原因" value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} placeholder="驳回或关闭必须填写具体原因" rows={4}/></p>
      {mutation.error ? <ErrorBox error={mutation.error}/> : null}
      <button className="btn primary" disabled={mutation.isPending} onClick={() => mutation.mutate('approve')}>批准，等待人工合并</button>{' '}
      <button className="btn" disabled={!reason.trim() || mutation.isPending} onClick={() => mutation.mutate('reject')}>驳回并排队重试</button>{' '}
      <button className="btn" disabled={!reason.trim() || mutation.isPending} onClick={() => mutation.mutate('close')}>关闭候选</button>
    </CardBody></Card> : null}
  </>;
}
