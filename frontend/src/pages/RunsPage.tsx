import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Form, Input, InputNumber, Modal, Select, Table, Typography } from 'antd';
import { CloseCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { api } from '../lib/api';
import { StatusTag } from '../components/StatusTag';
import type { RunStatus, RunSummary } from '../lib/types';
const { Title, Text } = Typography;
type LaunchValues = { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number };
export function RunsPage() {
  const navigate = useNavigate(); const { message } = App.useApp(); const queryClient = useQueryClient();
  const [page, setPage] = useState(1); const [keyword, setKeyword] = useState(''); const [status, setStatus] = useState<RunStatus | undefined>(); const [modalOpen, setModalOpen] = useState(false); const [form] = Form.useForm<LaunchValues>();
  const runs = useQuery({ queryKey: ['runs', page, keyword, status], queryFn: () => api.runs({ page, page_size: 20, keyword, status }), refetchInterval: 15000 });
  const start = useMutation({
    mutationFn: api.startRun,
    onSuccess: async ({ run_id }) => { setModalOpen(false); await queryClient.invalidateQueries({ queryKey: ['runs'] }); await queryClient.invalidateQueries({ queryKey: ['dashboard'] }); message.success('执行已创建'); navigate('/runs/' + encodeURIComponent(run_id)); },
    onError: (error) => message.error('创建执行失败：' + (error as Error).message),
  });
  const columns = [
    { title: '执行 ID', dataIndex: 'run_id', key: 'run_id', render: (v: string) => <a onClick={() => navigate('/runs/' + encodeURIComponent(v))}>{v}</a> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: RunSummary['status']) => <StatusTag status={v} /> },
    { title: '任务进度', key: 'progress', render: (_: unknown, row: RunSummary) => <span>{row.succeeded_tasks}/{row.task_count} 完成 · {row.failed_tasks} 失败 · {row.manual_tasks ?? 0} 人工介入</span> },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', render: (v: string) => dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '结束时间', dataIndex: 'finished_at', key: 'finished_at', render: (v?: string | null) => v && dayjs(v).isValid() ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—' },
    { title: '发起人', dataIndex: 'operator', key: 'operator', render: (v?: string) => v || '系统' },
    { title: '操作', key: 'actions', render: (_: unknown, row: RunSummary) => <Button type="link" onClick={() => navigate('/runs/' + encodeURIComponent(row.run_id))}>查看详情</Button> },
  ];
  return <div className="page-stack">
    <div className="page-heading"><div><Title level={3}>执行工作区</Title><Text type="secondary">按状态找到需要处理的轮次；打开后优先查看任务结论、根因证据与下一步操作。</Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => { form.setFieldsValue({ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }); setModalOpen(true); }}>新建执行</Button></div>
    {runs.isError && <Alert type="error" showIcon message="执行记录加载失败" description={(runs.error as Error).message} action={<Button type="link" onClick={() => runs.refetch()}>重试</Button>} />}
    <Card className="content-card"><div className="filter-row">
      <Input allowClear prefix={<SearchOutlined />} placeholder="搜索执行 ID" value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1); }} style={{ maxWidth: 300 }} />
      <Select allowClear placeholder="执行状态" value={status} onChange={(v) => { setStatus(v); setPage(1); }} style={{ width: 170 }} options={[{ value: 'queued', label: '排队中' }, { value: 'running', label: '执行中' }, { value: 'waiting_approval', label: '待审批' }, { value: 'succeeded', label: '已完成' }, { value: 'partial_failed', label: '部分失败' }, { value: 'failed', label: '失败' }, { value: 'cancelled', label: '已取消' }]} />
      <Button icon={<CloseCircleOutlined />} disabled={!keyword && !status} onClick={() => { setKeyword(''); setStatus(undefined); setPage(1); }}>清除筛选</Button><Button icon={<ReloadOutlined />} onClick={() => runs.refetch()} loading={runs.isFetching}>刷新</Button><Text type="secondary">每 15 秒自动更新</Text>
    </div><Table rowKey="run_id" loading={runs.isLoading} columns={columns} dataSource={runs.data?.items ?? []} pagination={{ current: page, pageSize: 20, total: runs.data?.total ?? 0, showSizeChanger: false, onChange: setPage, showTotal: (total) => '共 ' + total + ' 条' }} locale={{ emptyText: '没有符合条件的执行记录' }} /></Card>
    <Modal title="创建工作流执行" open={modalOpen} onCancel={() => !start.isPending && setModalOpen(false)} onOk={() => form.submit()} confirmLoading={start.isPending} okText="创建执行" cancelText="取消" destroyOnClose>
      <Alert type="info" showIcon message="创建后由服务端执行完整工作流" description="任务按既定规则串行处理。创建执行不会直接提交或合并代码。" className="modal-alert" />
      <Form form={form} layout="vertical" onFinish={(values) => start.mutate(values)} initialValues={{ log_days: 1, log_limit: 0, review_mode: 'agent', max_attempts: 3 }}>
        <Form.Item name="log_days" label="日志回溯天数" rules={[{ required: true }, { type: 'number', min: 1, max: 30, message: '范围为 1–30 天' }]}><InputNumber min={1} max={30} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="log_limit" label="日志数量上限" extra="0 表示使用服务端配置的默认策略" rules={[{ required: true }, { type: 'number', min: 0, max: 10000 }]}><InputNumber min={0} max={10000} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="review_mode" label="审查模式" rules={[{ required: true }]}><Select options={[{ value: 'agent', label: 'AI 审查 + 必要时人工审批' }, { value: 'human', label: '强制人工审查' }]} /></Form.Item>
        <Form.Item name="max_attempts" label="单任务最大修复尝试次数" rules={[{ required: true }, { type: 'number', min: 1, max: 5, message: '范围为 1–5 次' }]}><InputNumber min={1} max={5} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item><Text type="secondary">创建后无法从前端更改本轮已生效的执行配置。</Text></Form.Item>
      </Form>
    </Modal>
  </div>;
}