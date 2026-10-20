import { App, Form, InputNumber, Modal, Radio, Typography } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

const { Text } = Typography;

type FormValues = {
  log_days: number;
  log_limit: number;
  review_mode: 'agent' | 'human';
  max_attempts: number;
};

export function StartRunModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<FormValues>();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (values: FormValues) => api.startRun(values),
    onSuccess: async (result) => {
      message.success('执行已开始，正在打开管道画布');
      onClose();
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
      await queryClient.invalidateQueries({ queryKey: queryKeys.runsRoot });
      navigate(`/runs/${encodeURIComponent(result.run_id)}`);
    },
    onError: (error) => {
      message.error(error instanceof ApiError ? error.message : '发起执行失败');
    },
  });

  return (
    <Modal
      title="发起执行"
      open={open}
      onCancel={onClose}
      okText="发起执行"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      <Form<FormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }}
        onFinish={(values) => mutation.mutate(values)}
      >
        <Form.Item label="日志回溯天数" name="log_days" rules={[{ required: true, message: '请输入回溯天数' }]}>
          <InputNumber min={1} max={30} style={{ width: '100%' }} suffix="天" />
        </Form.Item>
        <Form.Item
          label="日志条数上限"
          name="log_limit"
          extra="0 表示采用服务端默认上限；强校验仍由后端执行。"
        >
          <InputNumber min={0} max={10000} style={{ width: '100%' }} suffix="条" />
        </Form.Item>
        <Form.Item label="审查模式" name="review_mode">
          <Radio.Group>
            <Radio.Button value="agent">AI 审查</Radio.Button>
            <Radio.Button value="human">人工审查</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <Text type="secondary" className="form-note">
          AI 审查：通过后自动交付修复分支，并进入交付审批。人工审查：跳过 AI 审查，回归通过后直接交付修复分支，由人工在交付审批里审查分支内容。
        </Text>
        <Form.Item label="最大修复尝试次数" name="max_attempts" rules={[{ required: true, message: '请输入尝试次数' }]}>
          <InputNumber min={1} max={5} style={{ width: '100%' }} suffix="次" />
        </Form.Item>
        <div className="form-callout">
          提交后全链路自动执行，操作会写入控制审计。除“交付审批”外无需人工介入。
        </div>
      </Form>
    </Modal>
  );
}
