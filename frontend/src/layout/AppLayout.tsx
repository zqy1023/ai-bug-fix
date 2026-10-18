import { useMemo, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AppstoreOutlined, BookOutlined, BellOutlined, CheckCircleOutlined, ClockCircleOutlined, CodeOutlined, FileSearchOutlined, HomeFilled, PlusOutlined, SafetyCertificateOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons';
import { Badge, Breadcrumb, Button, Input, Layout, Menu, Modal, Tooltip, Typography } from 'antd';
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
  const [workspace, setWorkspace] = useState('生产工作区');
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const authRequired = health.error instanceof ApiError && health.error.status === 401;
  const pendingCount = approvals.data?.length ?? 0;
  const selected = location.pathname.startsWith('/runs') ? '/runs' : location.pathname;
  const pageTitle = location.pathname.startsWith('/runs/') ? (taskId ? '任务审计' : '运行详情')
    : location.pathname === '/runs' ? '运行队列' : location.pathname === '/approvals' ? '待审批'
    : location.pathname === '/knowledge' ? '知识中心' : location.pathname === '/settings' ? '系统状态' : '运行概览';
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
    if (location.pathname === '/knowledge') return [...items, { title: '知识中心' }];
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
    <Modal title="切换工作区" open={workspaceOpen} onCancel={() => setWorkspaceOpen(false)} footer={null}>
      {['生产工作区', '测试工作区'].map((item) => <button key={item} className="workspace-choice" onClick={() => { setWorkspace(item); setWorkspaceOpen(false); }}>{item}<span>{item === workspace ? '当前' : '选择'}</span></button>)}
      <Text type="secondary">工作区切换仅更新当前界面标签；实际后端环境仍由服务端配置决定。</Text>
    </Modal>
    <Layout className="app-shell sketch-shell">
      <Sider width={249} collapsedWidth={249} collapsible={false} className="app-sider sketch-sider">
        <div className="brand sketch-brand">
          <div className="brand-mark"><CodeOutlined /></div>
          <div className="brand-copy"><strong>AI 异常修复</strong></div>
        </div>
        <button className="workspace-switcher" onClick={() => setWorkspaceOpen(true)}>
          <span className="status-dot is-online" /><strong>{workspace}</strong><span className="workspace-chevron">⌄</span>
        </button>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} className="workbench-menu sketch-menu"
          items={[
            { key: '/dashboard', icon: <HomeFilled />, label: '工作台' },
            { key: '/runs', icon: <ClockCircleOutlined />, label: <span className="menu-label"><span>运行队列</span><Badge count={pendingCount ? Math.max(pendingCount, 12) : 0} overflowCount={99} /></span> },
            { key: '/runs/audit', icon: <FileSearchOutlined />, label: '任务审计', disabled: true },
            { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span className="menu-label"><span>待审批</span><Badge count={pendingCount} overflowCount={99} /></span> },
            { type: 'divider' },
            { key: '/knowledge', icon: <BookOutlined />, label: '知识中心' },
            { key: '/settings', icon: <SettingOutlined />, label: '系统状态', disabled: true },
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
            <Tooltip title={pendingCount ? pendingCount + ' 项待审批' : '暂无待审批'}>
              <Button className="sketch-notification" type="text" icon={<Badge dot={pendingCount > 0}><BellOutlined /></Badge>} onClick={() => navigate('/approvals')} aria-label="通知" />
            </Tooltip>
            <button className="sketch-user" onClick={() => navigate('/knowledge')}><span>JD</span><strong>张三</strong><span className="workspace-chevron">⌄</span></button>
          </div>
        </Header>
        <Content className="page-content sketch-page-content"><Outlet /></Content>
      </Layout>
    </Layout>
  </>;
}
