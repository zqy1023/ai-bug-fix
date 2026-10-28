import { useEffect } from 'react';
import { Alert, App, Button, Form, InputNumber, Modal, Radio, Skeleton } from 'antd';
import { RocketOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { AgentPolicyFields } from './AgentPolicyFields';
import type { AgentPolicy } from '../../lib/types';
import styles from './StartRunModal.module.css';

type FormValues = {
  log_hours: number;
  review_mode: 'agent' | 'human';
  max_attempts: number;
  agent_policy?: AgentPolicy;
};

export function StartRunModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<FormValues>();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const config = useQuery({
    queryKey: queryKeys.runConfig,
    queryFn: api.runConfig,
    enabled: open,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    // Modal 销毁后仍复用 form 实例，必须显式同步本次加载的默认值。
    if (open && config.data && !config.isFetching && !config.isError) {
      form.setFieldsValue(config.data.defaults);
    }
  }, [open, config.data, config.isFetching, config.isError, form]);

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      if (!config.data || config.isFetching || config.isError) throw new Error('运行配置尚未就绪');
      return api.startRun({ ...values, log_limit: config.data.defaults.log_limit });
    },
    onSuccess: async (result) => {
      message.success('批次执行已创建，正在进入运行工作区');
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
      title={(
        <div className={styles.modalTitle}>
          <span className={styles.modalHeading}>新建执行批次</span>
          <span className={styles.modalSubheading}>全自动读取日志、按业务承运商归因定位并执行沙箱隔离修复</span>
        </div>
      )}
      open={open}
      width={700}
      wrapClassName={styles.modal}
      onCancel={onClose}
      okText="立即启动批次"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      okButtonProps={{
        disabled: !config.data || config.isFetching || config.isError,
        icon: <RocketOutlined />,
      }}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      {config.isFetching && <Skeleton active paragraph={{ rows: 6 }} />}
      {config.isError && (
        <Alert
          type="error"
          message="运行配置加载失败"
          description="请检查后端服务连接或工作台 API 状态。"
          action={<Button onClick={() => void config.refetch()}>重试</Button>}
        />
      )}
      {config.data && !config.isFetching && !config.isError && (
        <Form<FormValues>
          form={form}
          preserve={true}
          layout="vertical"
          requiredMark={false}
          initialValues={config.data.defaults}
          onFinish={(values) => mutation.mutate(values)}
          className={styles.formContent}
        >
          {/* 第一分区：基础运行参数 */}
          <div className={styles.section}>
            <div className={styles.sectionHeading}>
              <span className={styles.sectionBadge}>1</span>
              <span className={styles.sectionTitle}>执行范围与审查方式</span>
              <span className={styles.sectionSubtitle}>定义日志拉取窗口与门禁规则</span>
            </div>

            <div className={styles.parameterGrid}>
              <Form.Item
                label="回溯时间窗口"
                name="log_hours"
                extra={config.data.defaults.log_limit === 0 ? '按小时滚动全量读取' : `上限 ${config.data.defaults.log_limit} 条`}
                rules={[{ required: true, message: '请输入回溯小时数' }]}
              >
                <InputNumber
                  min={1}
                  max={config.data.limits.max_log_hours}
                  precision={0}
                  style={{ width: '100%' }}
                  suffix="小时"
                />
              </Form.Item>

              <Form.Item
                label="最大修复尝试次数"
                name="max_attempts"
                extra="单任务允许的最大重试修复轮数"
                rules={[{ required: true, message: '请输入尝试次数' }]}
              >
                <InputNumber
                  min={1}
                  max={config.data.limits.max_attempts}
                  precision={0}
                  style={{ width: '100%' }}
                  suffix="次"
                />
              </Form.Item>
            </div>

            <Form.Item label="审查模式" name="review_mode" style={{ marginBottom: 6 }}>
              <Radio.Group className={styles.reviewModeGroup}>
                <Radio.Button value="agent">AI 自动审查（通过后自动交付）</Radio.Button>
                <Radio.Button value="human">人工审查（跳过 AI，直接审批）</Radio.Button>
              </Radio.Group>
            </Form.Item>
            <span className={styles.note}>
              AI 审查模式将自动执行三维门禁并出具可信度评价；人工审查模式将跳过模型复核直接等待交付审批。
            </span>
          </div>

          {/* 第二分区：三个 Agent 模型配置与高级权限 */}
          <div className={styles.section}>
            <div className={styles.sectionHeading}>
              <span className={styles.sectionBadge}>2</span>
              <span className={styles.sectionTitle}>三个 Agent 协作模型与策略</span>
              <span className={styles.sectionSubtitle}>独立分配定位、修复与审查模型</span>
            </div>
            <AgentPolicyFields />
          </div>

          {/* 底部提醒条 */}
          <div className={styles.callout}>
            <span className={styles.calloutDot} />
            <span>提交后全链路在隔离沙箱中自动执行，操作自动记录控制审计；除“交付审批”外无需人工干预。</span>
          </div>
        </Form>
      )}
    </Modal>
  );
}

