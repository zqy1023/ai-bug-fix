import { useMemo, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  AppstoreOutlined, BookOutlined, CheckCircleOutlined, ClockCircleOutlined, CodeOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, SafetyCertificateOutlined, SearchOutlined,
} from '@ant-design/icons';
import { Badge, Breadcrumb, Button, Input, Layout, Menu, Modal, Space, Tooltip, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, setApiToken } from '../lib/api';

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const params = useParams();
  const [collapsed, setCollapsed] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');
  const [globalSearch, setGlobalSearch] = useState('');
  const selected = location.pathname.startsWith('/runs') ? '/runs' : location.pathname;
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const manual = useQuery({ queryKey: ['manual-interventions'], queryFn: api.manualInterventions, refetchInterval: 15000, retry: 1 });
  const authRequired = health.error instanceof ApiError && health.error.status === 401;
  const pendingCount = (approvals.data?.length ?? 0) + (manual.data?.length ?? 0);
  const crumbs = useMemo(() => {
    const base: { title: ReactNode }[] = [{ title: <a onClick={() => navigate('/dashboard')}>工作台</a> }];
    if (location.pathname === '/dashboard' || location.pathname === '/') return [...base, { title: '总览' }];
    if (location.pathname.startsWith('/runs')) {
      base.push({ title: <a onClick={() => navigate('/runs')}>执行工作区</a> });
      if (params.runId) base.push({ title: <a onClick={() => navigate('/runs/' + encodeURIComponent(params.runId!))}>执行详情</a> });
      if (params.taskId) base.push({ title: '任务检查' });
      return base;
    }
    if (location.pathname === '/approvals') return [...base, { title: '审批与人工介入' }];
    if (location.pathname === '/knowledge') return [...base, { title: '修复知识库' }];
    return [...base, { title: '工作区' }];
  }, [location.pathname, navigate, params.runId, params.taskId]);
  const pageTitle = location.pathname.startsWith('/runs/') ? (params.taskId ? '任务检查' : '执行详情')
    : location.pathname === '/runs' ? '执行工作区'
    : location.pathname === '/approvals' ? '审批与人工介入'
    : location.pathname === '/knowledge' ? '修复知识库' : '工作台总览';

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
    <Modal title="工作台身份验证" open={authRequired} closable={false} maskClosable={false} keyboard={false} okText="验证并连接" cancelButtonProps={{ style: { display: 'none' } }} onOk={submitToken} okButtonProps={{ disabled: !tokenDraft.trim(), loading: health.isFetching }}>
      <p>后端已启用 Bearer Token 认证。请输入工作台访问令牌后继续。</p>
      <Input.Password autoFocus value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} onPressEnter={submitToken} placeholder="工作台访问令牌" />
      <Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>令牌只保存在当前页面内存中，不写入浏览器存储或前端构建文件。刷新页面后需要重新验证。</Text>
    </Modal>
    <Layout className="app-shell">
      <Sider breakpoint="lg" collapsible collapsed={collapsed} onCollapse={setCollapsed} trigger={null} width={252} collapsedWidth={76} className="app-sider">
        <div className="brand">
          <div className="brand-mark"><CodeOutlined /></div>
          {!collapsed && <div className="brand-copy"><strong>AI 修复工作台</strong><span>RELIABILITY OPERATIONS</span></div>}
        </div>
        {!collapsed && <div className="sider-section-label">运营中心</div>}
        <Menu
          theme="light"
          mode="inline"
          selectedKeys={[selected]}
          items={[
            { key: 'operations', type: 'group', label: '工作空间', children: [
              { key: '/dashboard', icon: <AppstoreOutlined />, label: '工作台总览' },
              { key: '/runs', icon: <ClockCircleOutlined />, label: '执行工作区' },
              { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span className="menu-label">审批与人工介入 <Badge size="small" count={pendingCount} overflowCount={99} /></span> },
            ] },
            { key: 'governance', type: 'group', label: '知识与治理', children: [
              { key: '/knowledge', icon: <BookOutlined />, label: '修复知识库' },
            ] },
          ]}
          onClick={({ key }) => navigate(key)}
        />
        <div className="sider-bottom">
          <div className="sider-runtime">
            <span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />
            {!collapsed && <div><strong>工作流服务</strong><span>{health.isSuccess ? '连接正常' : health.isError ? '连接异常' : '检查中…'}</span></div>}
          </div>
          {!collapsed && <Text className="sider-version">串行执行 · 证据门禁 · 人工可控</Text>}
        </div>
      </Sider>
      <Layout className="main-layout">
        <Header className="app-header">
          <div className="header-left">
            <Button type="text" className="collapse-button" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} aria-label="折叠导航" />
            <div className="header-heading">
              <Breadcrumb items={crumbs} />
              <strong>{pageTitle}</strong>
            </div>
          </div>
          <Space size={12} className="header-right">
            <Input
              className="global-search"
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索执行 ID，回车打开"
              value={globalSearch}
              onChange={(event) => setGlobalSearch(event.target.value)}
              onPressEnter={submitSearch}
              aria-label="搜索执行 ID"
            />
            <Tooltip title={health.data ? '后端健康检查成功' : health.isError ? '后端健康检查失败' : '正在检查后端'}>
              <span className="health-indicator"><i className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />{health.isSuccess ? '服务在线' : health.isError ? '服务异常' : '检查中'}</span>
            </Tooltip>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/runs?new=1')}>新建执行</Button>
            <Button type="text" icon={<CheckCircleOutlined />} onClick={() => navigate('/approvals')} aria-label="前往审批与人工介入" title={'待处理 ' + pendingCount + ' 项'}>
              <Badge count={pendingCount} size="small" overflowCount={99} />
            </Button>
          </Space>
        </Header>
        <Content className="page-content"><Outlet /></Content>
        <Footer className="app-footer">
          <span>AI 异常日志自动修复工作流</span>
          <span>节点审计 · 源码证据 · 回归报告 · 人工决策</span>
        </Footer>
      </Layout>
    </Layout>
  </>;
}
