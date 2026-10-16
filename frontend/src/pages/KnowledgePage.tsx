import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import { BookOutlined, CheckCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import type { KnowledgeCase, KnowledgeCaseCreate, KnowledgeCaseStatus } from '../lib/types';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

type FormValues = KnowledgeCaseCreate;

function statusView(status: KnowledgeCaseStatus) {
  const values: Record<KnowledgeCaseStatus, { label: string; color: string }> = {
    candidate: { label: '待验证', color: 'gold' },
    published: { label: '已发布', color: 'green' },
    disabled: { label: '已停用', color: 'default' },
  };
  return values[status];
}

export function KnowledgePage() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<'all' | KnowledgeCaseStatus>('all');
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<KnowledgeCase | null>(null);
  const [form] = Form.useForm<FormValues>();
  const cases = useQuery({
    queryKey: ['knowledge-cases', keyword, status, page],
    queryFn: () => api.knowledgeCases({ q: keyword, status, page, page_size: 20 }),
    refetchInterval: 30000,
  });
  const create = useMutation({
    mutationFn: (values: FormValues) => api.createKnowledgeCase(values),
    onSuccess: async () => {
      message.success('案例已保存为候选知识，尚未发布');
      setCreateOpen(false);
      form.resetFields();
      await client.invalidateQueries({ queryKey: ['knowledge-cases'] });
    },
    onError: (error) => message.error('保存案例失败：' + (error as Error).message),
  });
  const updateStatus = useMutation({
    mutationFn: ({ item, next }: { item: KnowledgeCase; next: KnowledgeCaseStatus }) =>
      api.updateKnowledgeCaseStatus(item.case_id, next, next === 'published' ? '工作台人工发布' : '工作台状态调整'),
    onSuccess: async () => {
      message.success('知识状态已更新');
      setSelected(null);
      await client.invalidateQueries({ queryKey: ['knowledge-cases'] });
    },
    onError: (error) => message.error('更新知识状态失败：' + (error as Error).message),
  });

  const columns = [
    { title: '案例', dataIndex: 'title', key: 'title', render: (value: string, row: KnowledgeCase) => <div><a onClick={() => setSelected(row)}><strong>{value}</strong></a><div className="secondary-small">{row.fingerprint || '未填写异常指纹'}</div></div> },
    { title: '运输商 / 业务类型', key: 'scope', render: (_: unknown, row: KnowledgeCase) => <div>{row.carrier || '通用'}<div className="secondary-small">{row.biztype || '未指定 biztype'}</div></div> },
    { title: '验证门禁', key: 'gates', render: (_: unknown, row: KnowledgeCase) => <Space wrap><Tag color={row.regression_passed ? 'success' : 'default'}>回归{row.regression_passed ? '通过' : '未确认'}</Tag><Tag color={row.review_passed ? 'success' : 'default'}>审查{row.review_passed ? '通过' : '未确认'}</Tag></Space> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: KnowledgeCaseStatus) => <Tag color={statusView(value).color}>{statusView(value).label}</Tag> },
    { title: '被检索', dataIndex: 'usage_count', key: 'usage_count', sorter: (a: KnowledgeCase, b: KnowledgeCase) => a.usage_count - b.usage_count, render: (value: number) => value ?? 0 },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (value: string) => dayjs(value).isValid() ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—' },
    { title: '操作', key: 'actions', render: (_: unknown, row: KnowledgeCase) => <Space>
      <Button type="link" size="small" onClick={() => setSelected(row)}>详情</Button>
      {row.status === 'candidate' && <Button type="link" size="small" disabled={!row.regression_passed || !row.review_passed} loading={updateStatus.isPending} onClick={() => updateStatus.mutate({ item: row, next: 'published' })}>发布</Button>}
      {row.status === 'published' && <Button type="link" danger size="small" loading={updateStatus.isPending} onClick={() => updateStatus.mutate({ item: row, next: 'disabled' })}>停用</Button>}
      {row.status === 'disabled' && <Button type="link" size="small" loading={updateStatus.isPending} onClick={() => updateStatus.mutate({ item: row, next: 'candidate' })}>恢复候选</Button>}
    </Space> },
  ];

  return <div className="page-stack">
    <div className="page-heading">
      <div><Title level={3}>修复知识中心</Title><Text type="secondary">沉淀可追溯的根因、证据、修复方案和失败经验。新案例默认进入候选状态，经过验证后才能发布。</Text></div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setCreateOpen(true); }}>登记案例</Button>
    </div>
    {cases.isError && <Alert type="error" showIcon message="知识案例加载失败" description={(cases.error as Error).message} action={<Button type="link" onClick={() => cases.refetch()}>重试</Button>} />}
    <div className="knowledge-metrics">
      <Card className="metric-card"><Statistic title="全部案例" value={cases.data?.summary.total ?? 0} prefix={<BookOutlined />} loading={cases.isLoading} /></Card>
      <Card className="metric-card"><Statistic title="已发布知识" value={cases.data?.summary.published ?? 0} prefix={<CheckCircleOutlined />} loading={cases.isLoading} /></Card>
      <Card className="metric-card"><Statistic title="待验证候选" value={cases.data?.summary.candidate ?? 0} loading={cases.isLoading} /></Card>
      <Card className="metric-card"><Statistic title="已停用" value={cases.data?.summary.disabled ?? 0} prefix={<StopOutlined />} loading={cases.isLoading} /></Card>
      <Card className="metric-card"><Statistic title="历史检索次数" value={cases.data?.summary.retrievals ?? 0} prefix={<SearchOutlined />} loading={cases.isLoading} /></Card>
    </div>
    <Card className="content-card">
      <div className="filter-row">
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索案例、指纹、运输商或根因" value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} style={{ maxWidth: 360 }} />
        <Select value={status} onChange={(value) => { setStatus(value); setPage(1); }} style={{ width: 160 }} options={[{ value: 'all', label: '全部状态' }, { value: 'candidate', label: '待验证' }, { value: 'published', label: '已发布' }, { value: 'disabled', label: '已停用' }]} />
        <Button icon={<ReloadOutlined />} onClick={() => cases.refetch()} loading={cases.isFetching}>刷新</Button>
        <Text type="secondary">知识存储于工作台运行目录，独立于工作流步骤产物。</Text>
      </div>
      <Table rowKey="case_id" columns={columns} dataSource={cases.data?.items ?? []} loading={cases.isLoading} pagination={{ current: page, pageSize: 20, total: cases.data?.total ?? 0, showSizeChanger: false, onChange: setPage, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '暂无知识案例。可先登记历史修复经验，保存后将进入候选状态。' }} />
    </Card>

    <Modal title="登记修复案例" open={createOpen} onCancel={() => !create.isPending && setCreateOpen(false)} onOk={() => form.submit()} confirmLoading={create.isPending} okText="保存为候选" cancelText="取消" width={760} destroyOnClose>
      <Alert type="info" showIcon message="新案例不会自动发布" description="请如实填写证据与验证结果。只有回归和审查均通过的案例才允许发布；勾选项是人工录入声明，后续应由真实工作流报告自动关联验证证据。" style={{ marginBottom: 18 }} />
      <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)} >
        <Form.Item name="title" label="案例标题" rules={[{ required: true, min: 3, max: 200 }]}><Input maxLength={200} placeholder="例如：请求响应字段变化导致轨迹解析失败" /></Form.Item>
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="carrier" label="运输商" style={{ flex: 1 }}><Input maxLength={160} placeholder="明确运输商标识" /></Form.Item>
          <Form.Item name="biztype" label="业务类型" style={{ flex: 1 }}><Input maxLength={160} placeholder="严格使用真实 biztype" /></Form.Item>
        </Space>
        <Form.Item name="fingerprint" label="异常指纹 / 错误特征"><Input maxLength={500} placeholder="用于后续检索，不要填写密钥或完整敏感日志" /></Form.Item>
        <Form.Item name="root_cause" label="根因结论" rules={[{ required: true, min: 3, max: 4000 }]}><TextArea rows={3} maxLength={4000} showCount placeholder="说明已被证据支持的根因；未确认的部分应明确标注" /></Form.Item>
        <Form.Item name="evidence" label="证据与来源"><TextArea rows={3} maxLength={8000} showCount placeholder="记录日志特征、代码位置、报告路径或可追溯引用；不要粘贴凭据" /></Form.Item>
        <Form.Item name="change_summary" label="修复方案摘要"><TextArea rows={3} maxLength={8000} showCount placeholder="修改内容、适用条件、风险及已知限制" /></Form.Item>
        <Alert type="warning" showIcon message="验证状态由服务端核对真实运行记录" description="只有同时填写来源执行 ID 和任务 ID，且对应任务的回归步骤与审查步骤确有通过记录，案例才具备发布资格。前端不能手工勾选验证状态。" style={{ marginBottom: 16 }} />
        <Space style={{ display: 'flex' }} align="start">
          <Form.Item name="source_run_id" label="来源执行 ID" style={{ flex: 1 }}><Input maxLength={160} placeholder="真实执行 ID" /></Form.Item>
          <Form.Item name="source_task_id" label="来源任务 ID" style={{ flex: 1 }}><Input maxLength={160} placeholder="该执行中的 task_id" /></Form.Item>
        </Space>
      </Form>
    </Modal>

    <Modal title="知识案例详情" open={Boolean(selected)} onCancel={() => setSelected(null)} footer={<Button onClick={() => setSelected(null)}>关闭</Button>} width={760}>
      {selected && <div className="knowledge-detail">
        <Space wrap><Tag color={statusView(selected.status).color}>{statusView(selected.status).label}</Tag>{selected.carrier && <Tag>{selected.carrier}</Tag>}{selected.biztype && <Tag>{selected.biztype}</Tag>}</Space>
        <Title level={5}>{selected.title}</Title>
        <Text type="secondary">案例 ID：{selected.case_id}</Text>
        <Paragraph><strong>异常指纹</strong><br />{selected.fingerprint || '未填写'}</Paragraph>
        <Paragraph><strong>根因结论</strong><br />{selected.root_cause}</Paragraph>
        <Paragraph><strong>证据与来源</strong><br />{selected.evidence || '未填写'}</Paragraph>
        <Paragraph><strong>修复方案</strong><br />{selected.change_summary || '未填写'}</Paragraph>
        <Paragraph><strong>来源执行</strong><br />{selected.source_run_id || '未关联'}</Paragraph>
        <Space wrap><Tag color={selected.regression_passed ? 'success' : 'default'}>回归{selected.regression_passed ? '通过' : '未确认'}</Tag><Tag color={selected.review_passed ? 'success' : 'default'}>审查{selected.review_passed ? '通过' : '未确认'}</Tag></Space>
      </div>}
    </Modal>
  </div>;
}
