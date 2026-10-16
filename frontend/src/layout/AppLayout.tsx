import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  AppstoreOutlined, CheckCircleOutlined, ClockCircleOutlined, CodeOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Badge, Button, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

const routeMeta = [
  { key: '/dashboard', title: '运行控制台', eyebrow: 'CONTROL CENTER' },
  { key: '/runs', title: '执行记录', eyebrow: 'RUN HISTORY' },
  { key: '/approvals', title: '审批中心', eyebrow: 'HUMAN GATE' },
];

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const selected = location.pathname.startsWith('/runs/') ? '/runs' : location.pathname;
  const current = routeMeta.find((item) => item.key === selected) ?? { title: '执行详情', eyebrow: 'RUN INSPECTOR' };
  const health = useQuery({ queryKey: ['workbench-health'], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals, refetchInterval: 15000, retry: 1 });

  return <Layout className="app-shell">
    <Sider collapsible collapsed={collapsed} trigger={null} width={248} collapsedWidth={76} className="app-sider">
      <div className="brand">
        <div className="brand-mark"><CodeOutlined /></div>
        {!collapsed && <div className="brand-copy"><strong>AI 修复工作台</strong><span>INCIDENT OPERATIONS</span></div>}
      </div>
      {!collapsed && <div className="sider-section-label">工作空间</div>}
      <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={[
        { key: '/dashboard', icon: <AppstoreOutlined />, label: '运行控制台' },
        { key: '/runs', icon: <ClockCircleOutlined />, label: '执行与调试' },
        { key: '/approvals', icon: <SafetyCertificateOutlined />, label: <span className="menu-label">审批中心 <Badge size="small" count={approvals.data?.length ?? 0} overflowCount={99} /></span> },
      ]} onClick={({ key }) => navigate(key)} />
      <div className="sider-bottom">
        <div className="sider-runtime">
          <span className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />
          {!collapsed && <div><strong>工作流 API</strong><span>{health.isSuccess ? '连接正常' : health.isError ? '连接异常' : '检查中…'}</span></div>}
        </div>
        {!collapsed && <Text className="sider-version">真实运行数据 · 显式审批门禁</Text>}
      </div>
    </Sider>
    <Layout className="main-layout">
      <Header className="app-header">
        <div className="header-left">
          <Button type="text" className="collapse-button" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} aria-label="折叠导航" />
          <div className="header-heading"><span>{current.eyebrow}</span><strong>{current.title}</strong></div>
        </div>
        <Space size={12} className="header-right">
          <Tag bordered={false} color="blue">API CONTROL PLANE</Tag>
          <Tooltip title={health.data ? '后端健康检查成功' : health.isError ? '后端健康检查失败' : '正在检查后端'}>
            <span className="health-indicator"><i className={'status-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />{health.isSuccess ? '服务在线' : health.isError ? '服务异常' : '检查中'}</span>
          </Tooltip>
          <Button type="text" icon={<CheckCircleOutlined />} onClick={() => navigate('/approvals')} aria-label="前往审批中心" />
        </Space>
      </Header>
      <Content className="page-content"><Outlet /></Content>
      <Footer className="app-footer">
        <span>AI 异常日志自动修复工作流</span>
        <span>节点记录 · 模型审计 · 回归产物 · 人工审批</span>
      </Footer>
    </Layout>
  </Layout>;
}
