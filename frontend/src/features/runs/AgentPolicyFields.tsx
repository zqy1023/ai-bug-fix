import { Alert, Collapse, Form, InputNumber, Select, Switch } from 'antd';
import { SafetyCertificateOutlined, ToolOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { AgentPolicy } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import styles from './AgentPolicyFields.module.css';

type ToggleKey = keyof Pick<AgentPolicy,
  | 'analyze_legacy'
  | 'reproduce'
  | 'acceptance'
  | 'repair_legacy'
  | 'repair_shared'
  | 'allow_new_files'
  | 'allow_code_retirement'
  | 'verify_fixed'
  | 'request_regression'
  | 'automatic_repair'
  | 'local_commit'
  | 'remote_push'
>;

const DEFAULT_POLICY_TOGGLES: Record<ToggleKey, boolean> = {
  analyze_legacy: true,
  reproduce: true,
  acceptance: true,
  repair_legacy: false,
  repair_shared: false,
  allow_new_files: false,
  allow_code_retirement: false,
  verify_fixed: true,
  request_regression: true,
  automatic_repair: true,
  local_commit: true,
  remote_push: true,
};

type PolicyGroup = {
  key: string;
  label: string;
  items: Array<[ToggleKey, string]>;
};

const GROUPS: PolicyGroup[] = [
  {
    key: 'unified',
    label: '分析与受限修补权限',
    items: [
      ['analyze_legacy', '分析旧框架（仅分析交付）'],
      ['repair_legacy', '允许旧框架代码修补'],
      ['repair_shared', '允许指定共享目录修补'],
      ['allow_new_files', '允许新增代码文件'],
      ['allow_code_retirement', '允许注释停用旧代码（需 TODO）'],
    ],
  },
  {
    key: 'review',
    label: '审查与回归控制',
    items: [
      ['verify_fixed', '执行修复后对照验证'],
      ['request_regression', '执行真实请求业务回归'],
      ['automatic_repair', '审查不通过时自动回修'],
    ],
  },
  {
    key: 'delivery',
    label: '分支交付权限',
    items: [
      ['local_commit', '提交本地修复候选分支'],
      ['remote_push', '推送到远程候选分支'],
    ],
  },
];

const AGENT_ROLES = [
  {
    role: 'unified_fixer' as const,
    name: 'unified_model' as keyof AgentPolicy,
    title: '统一分析修复 Agent',
    desc: '端到端闭环：感知建模、根因定位、规则裁决、受限修补与自检反思',
    icon: <ToolOutlined className={styles.agentIcon} />,
  },
  {
    role: 'review' as const,
    name: 'review_model' as keyof AgentPolicy,
    title: 'AI 审查 Agent',
    desc: '改动核对、静态审查、三维门禁与全链路业务回归',
    icon: <SafetyCertificateOutlined className={styles.agentIcon} />,
  },
];

export function AgentPolicyFields() {
  const config = useQuery({
    queryKey: queryKeys.runConfig,
    queryFn: api.runConfig,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  return (
    <div className={styles.root}>
      {/* 核心 Agent 模型选择网格 */}
      <div className={styles.agentGrid}>
        {AGENT_ROLES.map(({ role, name, title, desc, icon }) => {
          const roleOptions = (config.data?.models?.[role] ?? []).filter((item) => item.available);
          const fallbackOptions = (config.data?.model_catalog ?? []).filter((item) => item.available);
          const options = roleOptions.length ? roleOptions : fallbackOptions;
          const defaultOption = (config.data?.models?.[role] ?? []).find((item) => item.is_default) ||
            config.data?.model_catalog?.find((item) => item.is_default);
          const unavailableReason = config.data?.model_catalog?.find((item) => item.reason)?.reason;
          return (
            <div key={role} className={styles.agentCard}>
              <div className={styles.agentHeader}>
                {icon}
                <span className={styles.agentTitle}>{title}</span>
              </div>
              <span className={styles.agentDesc}>{desc}</span>
              <Form.Item
                name={['agent_policy', name as string]}
                extra={
                  options.length
                    ? (defaultOption ? `默认继承：${defaultOption.label}` : '已探测到可用模型')
                    : (unavailableReason || '未探测到可用模型，默认采用服务配置')
                }
              >
                <Select
                  loading={config.isFetching}
                  disabled={config.isError || !options.length}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder={
                    config.isError
                      ? '模型目录加载失败'
                      : defaultOption
                        ? `默认使用 ${defaultOption.label}`
                        : '选择可用模型（默认继承）'
                  }
                  options={options.map((item) => ({
                    value: item.model_id,
                    label: item.is_default ? `${item.label}（默认）` : item.label,
                  }))}
                />
              </Form.Item>
            </div>
          );
        })}
      </div>

      {/* 高级策略折叠面板 */}
      <div className={styles.advancedWrap}>
        <Collapse
          ghost
          items={[
            {
              key: 'advanced',
              label: '高级权限与控制开关（可选）',
              forceRender: true,
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div className={styles.switchGrid}>
                    {GROUPS.flatMap((group) =>
                      group.items.map(([key, label]) => (
                        <div key={key} className={styles.switchRow}>
                          <span className={styles.switchLabel}>{label}</span>
                          <Form.Item
                            name={['agent_policy', key]}
                            valuePropName="checked"
                            initialValue={DEFAULT_POLICY_TOGGLES[key]}
                            noStyle
                          >
                            <Switch size="small" />
                          </Form.Item>
                        </div>
                      )),
                    )}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginTop: 4 }}>
                    <Form.Item
                      name={['agent_policy', 'shared_paths']}
                      initialValue={[]}
                      label={<span style={{ fontSize: 12 }}>共享目录白名单</span>}
                      extra="仅支持 common、framework、gathers_base、gather_providers"
                      style={{ marginBottom: 0 }}
                    >
                      <Select mode="tags" tokenSeparators={[',']} placeholder="例如 framework/result_format" size="small" />
                    </Form.Item>
                    <Form.Item
                      name={['agent_policy', 'max_repairs']}
                      initialValue={3}
                      label={<span style={{ fontSize: 12 }}>单任务最多回修轮数</span>}
                      style={{ marginBottom: 0 }}
                    >
                      <InputNumber min={1} max={10} precision={0} style={{ width: '100%' }} size="small" />
                    </Form.Item>
                  </div>

                  <Alert
                    type="info"
                    showIcon
                    message="权限不足或不可自动修补时智能提前收敛交付分析报告；策略将在任务准备阶段锁定快照。"
                    style={{ fontSize: 11.5, marginTop: 4 }}
                  />
                </div>
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}
