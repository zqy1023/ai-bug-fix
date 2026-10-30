import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHead,
  ErrorBox,
  Field,
  PageHeader,
  Skeleton,
  TextInput,
  useToast,
} from '../components/ui';
import type { ModelOption, RunConfig, RunDefaultsUpdate } from '../lib/types';

interface Draft {
  log_hours: number;
  log_limit: number;
  max_attempts: number;
  unified_model: string;
  review_model: string;
}

function draftOf(config?: RunConfig): Draft {
  const defaults = config?.defaults;
  return {
    log_hours: defaults?.log_hours ?? 12,
    log_limit: defaults?.log_limit ?? 0,
    max_attempts: defaults?.max_attempts ?? 3,
    unified_model: defaults?.agent_policy?.unified_model ?? '',
    review_model: defaults?.agent_policy?.review_model ?? '',
  };
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const config = useQuery({ queryKey: ['run-config'], queryFn: api.runConfig });
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 });
  const [draft, setDraft] = useState<Draft>(() => draftOf());

  useEffect(() => {
    if (config.data) setDraft(draftOf(config.data));
  }, [config.data]);

  const save = useMutation({
    mutationFn: () => {
      const current = config.data?.defaults;
      const payload: RunDefaultsUpdate = {
        log_hours: draft.log_hours,
        log_limit: draft.log_limit,
        max_attempts: draft.max_attempts,
      };
      const currentUnified = current?.agent_policy?.unified_model ?? '';
      const currentReview = current?.agent_policy?.review_model ?? '';
      if (draft.unified_model !== currentUnified) payload.unified_model = draft.unified_model || null;
      if (draft.review_model !== currentReview) payload.review_model = draft.review_model || null;
      return api.updateRunConfig(payload);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['run-config'], data);
      void queryClient.invalidateQueries({ queryKey: ['run-config'] });
      push('设置已保存', '只影响之后新建的批次');
    },
    onError: (error) => push('保存失败', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  const reset = useMutation({
    mutationFn: () => api.resetRunConfig(),
    onSuccess: (data) => {
      queryClient.setQueryData(['run-config'], data);
      void queryClient.invalidateQueries({ queryKey: ['run-config'] });
      setDraft(draftOf(data));
      push('已恢复环境默认值');
    },
    onError: (error) => push('恢复失败', error instanceof Error ? error.message : '请求失败', 'danger'),
  });

  const defaults = config.data?.defaults;
  const policy = defaults?.agent_policy;
  const nacos = config.data?.nacos;
  const canWrite = Boolean(nacos?.enabled);
  const unifiedOptions = config.data?.models.unified_fixer ?? config.data?.model_catalog ?? [];
  const reviewOptions = config.data?.models.review_agent ?? config.data?.model_catalog ?? [];
  const busy = save.isPending || reset.isPending;

  return (
    <>
      <PageHeader
        eyebrow="Settings / 设置"
        title="设置"
        meta="运行默认值可修改；环境变量 / Nacos 仍是底层默认。"
        actions={
          config.data?.customized ? <Badge tone="warn">已自定义</Badge> : <Badge tone="idle">环境默认</Badge>
        }
      />
      <div className="layout">
        <div>
          <Card>
            <CardHead
              title="运行默认值"
              sub={nacos ? `Nacos · ${nacos.data_id} / ${nacos.group}` : 'Nacos'}
              actions={
                <div className="row">
                  <Button size="sm" disabled={busy || !canWrite} onClick={() => reset.mutate()}>恢复默认</Button>
                  <Button size="sm" variant="primary" disabled={busy || !canWrite} onClick={() => save.mutate()}>
                    {save.isPending ? '保存中…' : '保存'}
                  </Button>
                </div>
              }
            />
            <CardBody>
              {config.isLoading ? <Skeleton rows={4} /> : config.error ? (
                <ErrorBox error={config.error} retry={() => void config.refetch()} />
              ) : (
                <>
                  <div className="two-col">
                    <Field label="时间窗口" hint={`小时；上限 ${config.data?.limits.max_log_hours ?? 720}。`}>
                      <TextInput
                        type="number"
                        min={1}
                        max={config.data?.limits.max_log_hours ?? 720}
                        value={draft.log_hours}
                        onChange={(event) => setDraft((current) => ({ ...current, log_hours: Number(event.target.value) || 1 }))}
                      />
                    </Field>
                    <Field label="日志上限" hint="0 表示全量拉取。">
                      <TextInput
                        type="number"
                        min={0}
                        value={draft.log_limit}
                        onChange={(event) => setDraft((current) => ({ ...current, log_limit: Math.max(0, Number(event.target.value) || 0) }))}
                      />
                    </Field>
                  </div>
                  <div className="two-col">
                    <Field label="最大尝试次数" hint={`含首次执行；上限 ${config.data?.limits.max_attempts ?? 5}。`}>
                      <TextInput
                        type="number"
                        min={1}
                        max={config.data?.limits.max_attempts ?? 5}
                        value={draft.max_attempts}
                        onChange={(event) => setDraft((current) => ({ ...current, max_attempts: Number(event.target.value) || 1 }))}
                      />
                    </Field>
                    <Field label="审查方式" hint="人工审批已移除，固定为 AI 自动审查。">
                      <TextInput value="AI 自动审查" readOnly />
                    </Field>
                  </div>
                  <div className="two-col">
                    <Field label="默认统一模型" hint="新批次未显式指定时使用。">
                      <ModelSelect
                        value={draft.unified_model}
                        options={unifiedOptions}
                        onChange={(value) => setDraft((current) => ({ ...current, unified_model: value }))}
                      />
                    </Field>
                    <Field label="默认审查模型" hint="新批次未显式指定时使用。">
                      <ModelSelect
                        value={draft.review_model}
                        options={reviewOptions}
                        onChange={(value) => setDraft((current) => ({ ...current, review_model: value }))}
                      />
                    </Field>
                  </div>
                  {!canWrite ? (
                    <p className="note" style={{ marginBottom: 10, color: 'var(--danger)' }}>
                      Nacos 未启用或不可达，当前只能查看；保存会直接写入 Nacos，不会落本地。
                    </p>
                  ) : null}
                  <p className="note" style={{ marginTop: 10 }}>
                    保存直接写入 Nacos 配置，只影响后续批次；已有批次保留原配置。
                  </p>
                </>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHead title="模型目录" sub="按角色" />
            <CardBody>
              {Object.entries(config.data?.models ?? {}).map(([role, options]) => (
                <div key={role} style={{ marginBottom: 14 }}>
                  <div className="card-sub" style={{ marginBottom: 6 }}>{role}</div>
                  <table className="table">
                    <tbody>
                      {options.map((model: ModelOption) => (
                        <tr key={model.key}>
                          <td><div className="row-title">{model.label}</div><div className="row-sub">{model.model_id}</div></td>
                          <td className="cell-right">
                            <span className={`badge ${model.available ? 'ok' : 'idle'}`}><i />{model.available ? '可用' : '不可用'}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
              {Object.keys(config.data?.models ?? {}).length === 0 && !config.isLoading ? <p className="note">没有模型目录。</p> : null}
            </CardBody>
          </Card>

          <Card>
            <CardHead title="Agent 策略" sub="环境默认，只读" />
            <CardBody>
              {policy ? (
                <>
                  <div className="two-col">
                    <dl className="kv">
                      <dt>分析历史</dt><dd>{policy.analyze_legacy ? '是' : '否'}</dd>
                      <dt>复现</dt><dd>{policy.reproduce ? '是' : '否'}</dd>
                      <dt>验收</dt><dd>{policy.acceptance ? '是' : '否'}</dd>
                      <dt>共享层</dt><dd>{policy.repair_shared ? '允许' : '不允许'}</dd>
                    </dl>
                    <dl className="kv">
                      <dt>新文件</dt><dd>{policy.allow_new_files ? '允许' : '不允许'}</dd>
                      <dt>自动回修</dt><dd>{policy.automatic_repair ? `是（${policy.max_repairs ?? 1} 次）` : '否'}</dd>
                      <dt>远程推送</dt><dd>{policy.remote_push ? '是' : '否'}</dd>
                    </dl>
                  </div>
                  <p className="note" style={{ marginTop: 10 }}>
                    策略开关决定写权限与推送边界，仍由环境变量 / Nacos 管理；设置页只允许改运行默认值和默认模型。
                  </p>
                </>
              ) : <p className="note">没有 Agent 策略配置。</p>}
            </CardBody>
          </Card>
        </div>

        <aside className="side">
          <Card>
            <CardHead title="系统健康" actions={<span className={`badge ${health.data?.status === 'ok' ? 'ok' : 'warn'}`}><i />{health.data?.status === 'ok' ? '正常' : '检查中'}</span>} />
            <CardBody>
              {health.isLoading ? <Skeleton rows={4} /> : health.error ? <ErrorBox error={health.error} retry={() => void health.refetch()} /> : (
                <dl className="kv">
                  <dt>服务</dt><dd>{health.data?.service}</dd>
                  <dt>版本</dt><dd>{health.data?.version}</dd>
                  <dt>源仓库</dt><dd>{health.data?.source_repo_configured ? '已配置' : '未配置'}</dd>
                  <dt>运行目录</dt><dd>{health.data?.runtime_dir_configured ? '已配置' : '未配置'}</dd>
                  <dt>API Token</dt><dd>{health.data?.api_token_required ? '已启用' : '未启用'}</dd>
                </dl>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHead title="配置层级" />
            <CardBody>
              <p className="note">优先级：新建批次显式参数 &gt; 设置页覆盖 &gt; 环境变量 / Nacos。</p>
              <div className="divider" />
              <p className="note">连接地址、凭据、源仓库、API Token 不在此页修改，避免界面写入与部署配置冲突。</p>
            </CardBody>
          </Card>
        </aside>
      </div>
    </>
  );
}

function ModelSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: ModelOption[];
  onChange: (value: string) => void;
}) {
  return (
    <select className="input mono" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">使用环境默认</option>
      {options.map((model) => (
        <option key={model.key} value={model.model_id} disabled={!model.available}>
          {model.label}{model.available ? '' : '（不可用）'}
        </option>
      ))}
    </select>
  );
}
