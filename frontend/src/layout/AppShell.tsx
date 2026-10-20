import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { ApiOutlined, SafetyCertificateOutlined, ThunderboltFilled, UnorderedListOutlined } from '@ant-design/icons';
import { Badge, Button, Input, Modal, Tooltip, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, setApiToken } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';

const { Text } = Typography;

const NAV = [
  { key: '/runs', label: '执行阵列', icon: <UnorderedListOutlined /> },
  { key: '/approvals', label: '交付审批', icon: <SafetyCertificateOutlined /> },
];

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [tokenDraft, setTokenDraft] = useState('');

  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ queryKey: queryKeys.approvals, queryFn: api.approvals, refetchInterval: 15000, retry: 1 });
  const authRequired = health.error instanceof ApiError && health.error.status === 401;
  const pending = approvals.data?.length ?? 0;

  const submitToken = async () => {
    if (!tokenDraft.trim()) return;
    setApiToken(tokenDraft);
    setTokenDraft('');
    await queryClient.invalidateQueries();
    await health.refetch();
  };

  const active = location.pathname.startsWith('/approvals') ? '/approvals' : '/runs';

  return (
    <div className="app-shell">
      <Modal
        title="连接工作流服务"
        open={authRequired}
        closable={false}
        maskClosable={false}
        keyboard={false}
        okText="验证并连接"
        cancelButtonProps={{ style: { display: 'none' } }}
        onOk={submitToken}
        okButtonProps={{ disabled: !tokenDraft.trim(), loading: health.isFetching }}
      >
        <p>后端已启用访问令牌认证。请输入工作台令牌后继续。</p>
        <Input.Password
          autoFocus
          value={tokenDraft}
          onChange={(event) => setTokenDraft(event.target.value)}
          onPressEnter={submitToken}
          placeholder="工作台访问令牌"
        />
        <Text type="secondary" className="token-note">令牌仅保存在当前页面内存中，刷新后需要重新验证。</Text>
      </Modal>

      <header className="app-nav">
        <button type="button" className="brand" onClick={() => navigate('/runs')}>
          <span className="brand-mark"><ThunderboltFilled /></span>
          <span className="brand-copy">
            <strong>AI 修复流水线</strong>
            <small>LangGraph Control Plane</small>
          </span>
        </button>

        <nav className="app-nav-links" aria-label="主导航">
          {NAV.map((item) => (
            <button
              key={item.key}
              type="button"
              className={'nav-link' + (active === item.key ? ' is-active' : '')}
              onClick={() => navigate(item.key)}
            >
              {item.icon}
              <span>{item.label}</span>
              {item.key === '/approvals' && pending > 0 && (
                <Badge count={pending} overflowCount={99} size="small" className="nav-badge" />
              )}
            </button>
          ))}
        </nav>

        <div className="app-nav-right">
          <Tooltip title={health.isSuccess ? '工作流服务连接正常' : health.isError ? '工作流服务不可用' : '正在检查服务状态'}>
            <span className="health-pill">
              <span className={'health-dot ' + (health.isSuccess ? 'is-online' : health.isError ? 'is-offline' : 'is-checking')} />
              {health.isSuccess ? '服务正常' : health.isError ? '服务异常' : '检查中'}
            </span>
          </Tooltip>
          <Button
            type="text"
            size="small"
            icon={<ApiOutlined />}
            onClick={() => queryClient.invalidateQueries()}
            title="刷新全部数据"
          />
        </div>
      </header>

      <main className="app-main"><Outlet /></main>

      <footer className="app-statusbar">
        <span>
          <span className={'health-dot ' + (health.isSuccess ? 'is-online' : 'is-offline')} />
          {health.data ? `${health.data.service} · v${health.data.version}` : '未连接'}
        </span>
        <span>运行目录 {health.data?.runtime_dir_configured ? '已配置' : '未配置'}</span>
        <span>源仓库 {health.data?.source_repo_configured ? '已配置' : '未配置'}</span>
        <span className="statusbar-tail">{pending > 0 ? `${pending} 个任务待交付审批` : '无待审批任务'}</span>
      </footer>
    </div>
  );
}
