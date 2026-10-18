import { useMemo, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { CheckCircleOutlined, ClockCircleOutlined, CodeOutlined, HomeFilled, PlusOutlined, SafetyCertificateOutlined, SearchOutlined,  } from '@ant-design/icons';
import { Badge, Breadcrumb, Button, Input, Layout, Menu, Modal, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, setApiToken } from '../lib/api';

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeParts = location.pathname.split('/').filter(Boolean);
  const runId = routeParts[0] === 'runs' && routeParts[1] ? decodeURIComponent(routeParts[1]) : '';
  const taskId = routeParts[2] === 'tasks' && routeParts[3] ? decodeURIComponent(routeParts[3]) : '';
  const [tokenDraft, setTokenDraft] = useState('');
  const [globalSearch, setGlobalSearch] = useState('');
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const authRequired = health.error instanceof ApiError && health.error.status === 401;
  const pendingCount = approvals.data?.length ?? 0;
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15000, retry: 1 });
  const activeRunCount = dashboard.data?.active_runs ?? 0;
  const selected = location.pathname.startsWith('/runs') ? '/runs' : location.pathname;
  const pageTitle = location.pathname.startsWith('/runs/') ? (taskId ? '任务审计' : '运行详情')
    : location.pathname === '/runs' ? '运行队列' : location.pathname === '/approvals' ? '待审批'
    : '运行概览';
  const crumbs = useMemo(() => {
    const items: { title: ReactNode }[] = [{ title: <a onClick={() => navigate('/dashboard')}>工作台</a> }];
    if (location.pathname === '/' || location.pathname === '/dashboard') return [...items, { title: '运行概览' }];
    if (location.pathname.startsWith('/runs')) {
      items.push({ title: <a onClick={() => navigate('/runs')}>运行队列</a> });
      if (runId) items.push({ title: <a onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>{runId}</a> });
      if (taskId) items.push({ title: taskId });
      return items;
    }
    if (location.pathname === '/approvals') return [...items, { title: '待审批' }];
    return [...items, { title: pageTitle }];
  }, [location.pathname, navigate, runId, taskId, pageTitle]);
  const submitToken = async () => {
    if (!tokenDraft.trim()) return;
    setApiToken(tokenDraft);
    setTokenDraft('');
    await queryClient.invalidateQueries();
    await health.refetch();
  };
  const submitSearch = () => {
    const keyword = globalSearch.trim();
    if (!keyword) return;
    navigate('/runs?keyword=' + encodeURIComponent(keyword));
    setGlobalSearch('');
  };

  return <>
    <Modal title="连接工作流服务" open={authRequired} closable={false} maskClosable={false} keyboard={false}
      okText="验证并连接" cancelButtonProps={{ style: { display: 'none' } }} onOk={submitToken}
      okButtonProps={{ disabled: !tokenDraft.trim(), loading: health.isFetching }}>
      <p>后端已启用访问令牌认证。请输入工作台令牌后继续。</p>
      <Input.Password autoFocus value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)}
        onPressEnter={submitToken} placeholder="工作台访问令牌" />
      <Text type="secondary" className="token-note">令牌仅保存在当前页面内存中，刷新后需要重新验证。</Text>
    </Modal>
    <Layout className="app-shell sketch-shell">
      <Sider width={249} collapsedWidth={249} collapsible={false} className="app-sider sketch-sider">
        <div className="brand sketch-brand">
          <div className="brand-mark"><CodeOutlined /></div>
          <div className="brand-copy"><strong>AI 异常修复</strong></div>
        </div>
        <div className="workspace-switcher workspace-label" aria-label="当前服务端配置的工作流环境">
          <span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} /><strong>工作流服务</strong>
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} className="workbench-menu sketch-menu"
          items={[
            { key: '/dashboard', icon: <HomeFilled />, label: '工作台' },
            { key: '/runs', icon: <ClockCircleOutlined />, label: <span className="menu-label"><span>运行队列</span><Badge count={activeRunCount} overflowCount={99} /></span> },
            
            { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span className="menu-label"><span>待审批</span><Badge count={pendingCount} overflowCount={99} /></span> },
            { type: 'divider' },
            
          ]}
          onClick={({ key }) => navigate(key)} />
        <div className="sider-bottom sketch-sider-bottom">
          <div className="sider-runtime"><span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} /><div><strong>{health.isSuccess ? '服务正常' : health.isError ? '服务异常' : '检查服务状态'}</strong></div></div>
        </div>
      </Sider>
      <Layout className="main-layout sketch-main-layout">
        <Header className="app-header sketch-header">
          <div className="sketch-breadcrumb"><Breadcrumb items={crumbs} /></div>
          <div className="header-right sketch-header-right">
            <Input className="global-search sketch-search" allowClear prefix={<SearchOutlined />} placeholder="搜索 Run ID、运输商、异常关键词"
              value={globalSearch} onChange={(event) => setGlobalSearch(event.target.value)} onPressEnter={submitSearch} aria-label="搜索运行记录" />
            <Button className="sketch-approval-entry" type={location.pathname === '/approvals' ? 'primary' : 'default'} icon={<SafetyCertificateOutlined />} onClick={() => navigate('/approvals')}>待审批 <Badge count={pendingCount} overflowCount={99} /></Button>
          </div>
        </Header>
        <Content className="page-content sketch-page-content"><Outlet /></Content>
      </Layout>
    </Layout>
  </>;
}
