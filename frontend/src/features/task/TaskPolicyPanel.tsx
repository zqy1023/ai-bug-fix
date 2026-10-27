import { Alert, App, Button, Form } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AgentPolicyFields } from '../runs/AgentPolicyFields';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { AgentPolicy, TaskDetailResponse } from '../../lib/types';

export function TaskPolicyPanel({detail}:{detail:TaskDetailResponse}) {
  const [form] = Form.useForm<{agent_policy:AgentPolicy}>();
  const client=useQueryClient();const {message}=App.useApp();
  const locked=detail.read_only||detail.policy?.frozen||detail.task.steps.length>0;
  const save=useMutation({mutationFn:(values:{agent_policy:AgentPolicy})=>api.setTaskPolicy(detail.run_id,detail.task.task_id,values.agent_policy as unknown as Record<string,unknown>),onSuccess:async()=>{message.success('任务策略已保存');await client.invalidateQueries({queryKey:queryKeys.task(detail.run_id,detail.task.task_id)});},onError:(error)=>message.error(error.message)});
  if(!detail.policy)return null;
  return <section style={{marginBottom:24}}>
    <h3>模型执行策略</h3>
    <Alert type="info" message={locked?'当前任务策略已冻结，下列为实际生效值':'任务尚未开始，可覆盖批次策略'} />
    <Form form={form} layout="vertical" disabled={locked} initialValues={{agent_policy:detail.policy.values}} onFinish={values=>save.mutate(values)}>
      <AgentPolicyFields />
      {!locked&&<Button type="primary" htmlType="submit" loading={save.isPending}>保存任务策略</Button>}
    </Form>
    <details><summary>策略来源</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(detail.policy.sources,null,2)}</pre></details>
  </section>;
}
