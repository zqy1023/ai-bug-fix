import { Alert, Collapse, Form, InputNumber, Select, Switch } from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { AgentPolicy } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

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

type PolicyGroup = {
  key: string;
  label: string;
  items: Array<[ToggleKey, string]>;
};

const GROUPS: PolicyGroup[] = [
  {
    key: 'analysis',
    label: '分析模型',
    items: [
      ['analyze_legacy', '分析旧框架'],
      ['reproduce', '执行修复前基线复现'],
      ['acceptance', '生成任务验收条件'],
    ],
  },
  {
    key: 'repair',
    label: '修复模型权限',
    items: [
      ['repair_legacy', '允许旧框架修复'],
      ['repair_shared', '允许指定共享目录修复'],
      ['allow_new_files', '允许新增文件'],
      ['allow_code_retirement', '允许注释停用旧代码（必须有 TODO）'],
    ],
  },
  {
    key: 'review',
    label: '审查与回修',
    items: [
      ['verify_fixed', '执行修复后对照验证'],
      ['request_regression', '执行真实请求业务回归'],
      ['automatic_repair', '审查不通过时自动回修'],
    ],
  },
  {
    key: 'delivery',
    label: '报告与交付',
    items: [
      ['local_commit', '提交本地分支'],
      ['remote_push', '推送远程分支'],
    ],
  },
];

export function AgentPolicyFields() {
  const config = useQuery({
    queryKey: queryKeys.runConfig,
    queryFn: api.runConfig,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const modelField = (role: 'analysis' | 'repair' | 'review', label: string, name: keyof AgentPolicy) => {
    const options = (config.data?.models?.[role] ?? []).filter((item) => item.available);
    const unavailableReason = config.data?.model_catalog?.find((item) => item.reason)?.reason;
    return (
      <Form.Item key={role} name={['agent_policy', name]} label={label} extra={options.length ? undefined : unavailableReason || '当前模型服务没有返回可用模型，请检查 ANTHROPIC_BASE_URL、ANTHROPIC_AUTH_TOKEN 和 provider 的 /v1/models。'}>
        <Select
          loading={config.isFetching}
          disabled={config.isError || !options.length}
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={config.isError ? '模型目录加载失败' : '选择真实可用模型'}
          options={options.map((item) => ({ value: item.model_id, label: item.is_default ? `${item.label}（默认）` : item.label }))}
        />
      </Form.Item>
    );
  };
  return (
    <>
      <Collapse
        items={GROUPS.map((group) => ({
          key: group.key,
          label: group.label,
          children: (
            <>
              {group.items.map(([key, label]) => (
                <Form.Item key={key} name={['agent_policy', key]} label={label} valuePropName="checked">
                  <Switch />
                </Form.Item>
              ))}
              {group.key === 'repair' && (
                <Form.Item
                  name={['agent_policy', 'shared_paths']}
                  label="共享目录白名单"
                  extra="仅支持 common、framework、gathers_base、gather_providers 下的明确路径。"
                >
                  <Select mode="tags" tokenSeparators={[',']} placeholder="例如 framework/result_format" />
                </Form.Item>
              )}
              {group.key === 'review' && (
                <Form.Item name={['agent_policy', 'max_repairs']} label="最多执行修复轮数">
                  <InputNumber min={1} max={10} precision={0} />
                </Form.Item>
              )}
              {group.key === 'delivery' && (
                <Alert
                  type="info"
                  message="权限不足时交付分析报告；缺少 response 只标记业务未验证。策略在任务准备阶段冻结。"
                />
              )}
            </>
          ),
        }))}
      />
      {modelField('analysis', '分析模型', 'analysis_model')}
      {modelField('repair', '修复模型', 'repair_model')}
      {modelField('review', '审查模型', 'review_model')}
    </>
  );
}
