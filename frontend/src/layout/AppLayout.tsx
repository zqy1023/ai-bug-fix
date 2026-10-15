import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AlertOutlined, AppstoreOutlined, CheckCircleOutlined, ClockCircleOutlined, MenuFoldOutlined, MenuUnfoldOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Badge, Button, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd';
import { isDemoMode } from '../lib/api';
const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;
const titles: Record<string, string> = { '/dashboard': '运行总览', '/runs': '执行记录', '/approvals': '人工审批' };
export function AppLayout() {
  const location = useLocation(); const navigate = useNavigate(); const [collapsed, setCollapsed] = useState(false);
  const selected = location.pathname.startsWith('/runs/') ? '/runs' : location.pathname;
  return <Layout className="app-shell">
    <Sider collapsible collapsed={collapsed} trigger={null} width={236} collapsedWidth={72} className="app-sider">
      <div className="brand"><div className="brand-mark">AI</div>{!collapsed && <div className="brand-copy"><strong>修复工作台</strong><span>AI INCIDENT OPS</span></div>}</div>
      <div className="sider-section-label">{!collapsed && '工作空间'}</div>
      <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={[
        { key: '/dashboard', icon: <AppstoreOutlined />, label: '运行总览' },
        { key: '/runs', icon: <ClockCircleOutlined />, label: '执行记录' },
        { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span>人工审批 <Badge size="small" count={0} /></span> },
      ]} onClick={({ key }) => navigate(key)} />
      <div className="sider-bottom"><div className="sider-status"><span className="status-dot" />{!collapsed && '工作流服务'}</div>{!collapsed && <Text type="secondary" className="sider-version">Workbench · 0.1.0</Text>}</div>
    </Sider>
    <Layout className="main-layout">
      <Header className="app-header">
        <Space size={14}><Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} aria-label="折叠导航" /><div className="header-title">{titles[selected] ?? '执行详情'}</div><Text type="secondary" className="header-subtitle">异常发现 · AI 修复 · 验证交付</Text></Space>
        <Space size={12}>{isDemoMode ? <Tag color="gold">演示模式</Tag> : <Tag color="blue">API 模式</Tag>}<Tooltip title="状态由后端健康检查确认"><Badge status="default" text={<span className="header-service">服务状态</span>} /></Tooltip><Button type="text" icon={<AlertOutlined />} aria-label="通知" onClick={() => navigate('/approvals')} /></Space>
      </Header>
      <Content className="page-content"><Outlet /></Content>
      <Footer className="app-footer"><span>AI 异常日志自动修复工作流</span><span><CheckCircleOutlined /> 人工审批与 Git 交付保留显式门禁</span></Footer>
    </Layout>
  </Layout>;
}