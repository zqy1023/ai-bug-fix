import { useMemo, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AppstoreOutlined, BookOutlined, CheckCircleOutlined, ClockCircleOutlined, CodeOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { Badge, Breadcrumb, Button, Input, Layout, Menu, Modal, Tooltip, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, setApiToken } from '../lib/api';

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeParts = location.pathname.split('/').filter(Boolean);
  const runId = routeParts[0] === 'runs' && routeParts[1] ? decodeURIComponent(routeParts[1]) : '';
  const taskId = routeParts[2] === 'tasks' && routeParts[3] ? decodeURIComponent(routeParts[3]) : '';
  const [collapsed, setCollapsed] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');
  const [globalSearch, setGlobalSearch] = useState('');
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const authRequired = health.error instanceof ApiError && health.error.status === 401;
  const pendingCount = approvals.data?.length ?? 0;
  const selected = location.pathname.startsWith('/runs') ? '/runs' : location.pathname;

  const pageTitle = location.pathname.startsWith('/runs/')
    ? (taskId ? '任务诊断' : '运行详情')
    : location.pathname === '/runs' ? '执行记录'
    : location.pathname === '/approvals' ? '最终审批'
    : location.pathname === '/knowledge' ? '修复知识库'
    : '异常分诊台';

  const crumbs = useMemo(() => {
    const items: { title: ReactNode }[] = [{ title: <a onClick={() => navigate('/dashboard')}>修复工作台</a> }];
    if (location.pathname === '/dashboard' || location.pathname === '/') return [...items, { title: '异常分诊台' }];
    if (location.pathname.startsWith('/runs')) {
      items.push({ title: <a onClick={() => navigate('/runs')}>执行记录</a> });
      if (runId) items.push({ title: <a onClick={() => navigate('/runs/' + encodeURIComponent(runId))}>{runId}</a> });
      if (taskId) items.push({ title: taskId });
      return items;
    }
    if (location.pathname === '/approvals') return [...items, { title: '最终审批' }];
    if (location.pathname === '/knowledge') return [...items, { title: '修复知识库' }];
    return [...items, { title: '工作区' }];
  }, [location.pathname, navigate, runId, taskId]);

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

  return (
    <>
      <Modal title="连接工作流服务" open={authRequired} closable={false} maskClosable={false} keyboard={false}
        okText="验证并连接" cancelButtonProps={{ style: { display: 'none' } }} onOk={submitToken}
        okButtonProps={{ disabled: !tokenDraft.trim(), loading: health.isFetching }}>
        <p>后端已启用访问令牌认证。请输入工作台令牌后继续。</p>
        <Input.Password autoFocus value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)}
          onPressEnter={submitToken} placeholder="工作台访问令牌" />
        <Text type="secondary" className="token-note">令牌仅保存在当前页面内存中，不写入浏览器存储或前端构建文件。刷新后需要重新验证。</Text>
      </Modal>

      <Layout className="app-shell">
        <Sider width={252} collapsedWidth={76} collapsible collapsed={collapsed} onCollapse={setCollapsed}
          trigger={null} breakpoint="lg" className="app-sider">
          <div className="brand">
            <div className="brand-mark"><CodeOutlined /></div>
            {!collapsed && <div className="brand-copy"><strong>AI 异常修复</strong><span>RELIABILITY WORKBENCH</span></div>}
          </div>
          {!collapsed && <div className="sider-section-label">工作空间</div>}
          <Menu theme="dark" mode="inline" selectedKeys={[selected]} className="workbench-menu"
            items={[
              { key: '/dashboard', icon: <AppstoreOutlined />, label: '异常分诊台' },
              { key: '/runs', icon: <ClockCircleOutlined />, label: '执行记录' },
              { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span className="menu-label"><span>最终审批</span><Badge count={pendingCount} overflowCount={99} /></span> },
              { key: '/knowledge', icon: <BookOutlined />, label: '修复知识库' },
            ]}
            onClick={({ key }) => navigate(key)} />
          <div className="sider-bottom">
            <div className="sider-runtime">
              <span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />
              {!collapsed && <div><strong>工作流服务</strong><span>{health.isSuccess ? '连接正常' : health.isError ? '连接异常' : '检查中…'}</span></div>}
            </div>
            {!collapsed && <div className="sider-version">串行执行 · 证据门禁 · 可审计交付</div>}
          </div>
        </Sider>

        <Layout className="main-layout">
          <Header className="app-header">
            <div className="header-left">
              <Button type="text" className="collapse-button" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => setCollapsed(!collapsed)} aria-label="折叠导航" />
              <div className="header-heading"><Breadcrumb items={crumbs} /><strong>{pageTitle}</strong></div>
            </div>
            <div className="header-right">
              <Input className="global-search" allowClear prefix={<SearchOutlined />} placeholder="搜索执行 ID 或关键词"
                value={globalSearch} onChange={(event) => setGlobalSearch(event.target.value)}
                onPressEnter={submitSearch} aria-label="搜索执行记录" />
              <Tooltip title={health.isSuccess ? '后端健康检查成功' : health.isError ? '后端健康检查失败' : '正在检查后端'}>
                <span className="health-indicator"><i className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />
                  {health.isSuccess ? '服务在线' : health.isError ? '服务异常' : '检查中'}</span>
              </Tooltip>
              <Button type="primary" icon={<PlusOutlined />} disabled={Boolean(health.data && !health.data.source_repo_configured)} onClick={() => navigate('/runs?new=1')}>新建扫描</Button>
              <Tooltip title={pendingCount ? pendingCount + ' 项待最终审批' : '暂无待审批'}>
                <Button className="approval-shortcut" type="text" icon={<CheckCircleOutlined />} onClick={() => navigate('/approvals')}>
                  <Badge count={pendingCount} size="small" overflowCount={99} />
                </Button>
              </Tooltip>
            </div>
          </Header>
          <Content className="page-content"><Outlet /></Content>
          <Footer className="app-footer"><span>AI 异常日志自动修复工作流</span><span>任务分诊 · 执行追踪 · 证据审查 · 最终审批</span></Footer>
        </Layout>
      </Layout>
    </>
  );
}
