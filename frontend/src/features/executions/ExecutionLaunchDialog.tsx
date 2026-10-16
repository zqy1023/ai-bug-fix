import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Form, InputNumber, Modal, Select } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';

type LaunchValues = { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number };

export function ExecutionLaunchDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [form] = Form.useForm<LaunchValues>();
  const start = useMutation({
    mutationFn: api.startRun,
    onSuccess: async ({ run_id }) => {
      onClose();
      message.success('执行已创建，正在进入执行工作区');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['dashboard'] }),
        client.invalidateQueries({ queryKey: ['runs'] }),
      ]);
      navigate('/runs/' + encodeURIComponent(run_id));
    },
    onError: (error) => message.error('创建执行失败：' + (error as Error).message),
  });

  return <Modal
    title="开始异常扫描"
    open={open}
    onCancel={() => !start.isPending && onClose()}
    onOk={() => form.submit()}
    confirmLoading={start.isPending}
    okText="确认并开始"
    cancelText="返回修改"
    destroyOnClose
  >
    <Alert
      type="info"
      showIcon
      message="启动后任务按工作流规则串行处理"
      description="这一步只创建执行，不会直接提交或合并代码。遇到共享层变更、证据不足或审查风险时，工作流会进入相应门禁。"
      style={{ marginBottom: 20 }}
    />
    <Form
      form={form}
      layout="vertical"
      onFinish={(values) => start.mutate(values)}
      initialValues={{ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }}
    >
      <Form.Item name="log_days" label="扫描最近多少天的日志" rules={[{ required: true }, { type: 'number', min: 1, max: 30, message: '范围为 1–30 天' }]}>
        <InputNumber min={1} max={30} precision={0} style={{ width: '100%' }} />
      </Form.Item>
      <Form.Item name="log_limit" label="日志数量上限" extra="0 表示采用服务端配置的默认策略" rules={[{ required: true }, { type: 'number', min: 0, max: 10000 }]}>
        <InputNumber min={0} max={10000} precision={0} style={{ width: '100%' }} />
      </Form.Item>
      <Form.Item name="review_mode" label="变更审查方式" rules={[{ required: true }]}>
        <Select options={[{ value: 'agent', label: 'AI 审查，必要时转人工' }, { value: 'human', label: '所有任务强制人工审查' }]} />
      </Form.Item>
      <Form.Item name="max_attempts" label="每个任务最多修复尝试次数" rules={[{ required: true }, { type: 'number', min: 1, max: 5, message: '范围为 1–5 次' }]}>
        <InputNumber min={1} max={5} precision={0} style={{ width: '100%' }} />
      </Form.Item>
    </Form>
  </Modal>;
}
