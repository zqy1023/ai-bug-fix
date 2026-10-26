import { workbenchQueries } from '../lib/workbenchQueries';
import { useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  MoonOutlined, SafetyCertificateOutlined, SunOutlined, ToolOutlined, TableOutlined, MenuFoldOutlined, MenuUnfoldOutlined, ApartmentOutlined, ReloadOutlined, QuestionCircleOutlined, BellOutlined, UserOutlined, ArrowRightOutlined,
} from '@ant-design/icons';
import { Button, Drawer, Input, Modal, Popover, Tooltip, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, setApiToken } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useThemeMode } from '../app/ThemeProvider';
import { WorkspaceSearch } from './WorkspaceSearch';
import styles from './AppShell.module.css';

const { Text } = Typography;

const NAV_GROUPS = [
  { label: '执行', items: [{ key: '/runs', label: '执行批次', icon: <TableOutlined /> }] },
  { label: '人工', items: [{ key: '/approvals', label: '交付审批', icon: <SafetyCertificateOutlined /> }] },
];

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { mode, toggle } = useThemeMode();
  const [tokenDraft, setTokenDraft] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const approvals = useQuery({ ...workbenchQueries.approvals(), retry: 1 });
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
  const healthDotClass = health.isSuccess
    ? `${styles.healthDot} ${styles.dotOnline}`
    : health.isError ? `${styles.healthDot} ${styles.dotOffline}` : `${styles.healthDot} ${styles.dotChecking}`;

  return (
    <div className={`${styles.shell}${collapsed ? ` ${styles.collapsed}` : ''}`}>
      <a className={styles.skipLink} href="#main-content">跳到主要内容</a>
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
        <Text type="secondary" className={styles.tokenNote}>令牌仅保存在当前页面内存中，刷新后需要重新验证。</Text>
      </Modal>

      <aside className={styles.side}>
        <div className={styles.sideTop}>
          <div className={styles.brandRow}>
            <Link className={styles.brand} to="/runs" aria-label="修复工作台首页">
              <ToolOutlined className={styles.brandMark} />
              <span className={styles.brandCopy}><strong>修复工作台</strong><small>Repair Atlas</small></span>
            </Link>
            <button className={styles.collapseHandle} aria-label={collapsed ? '展开导航' : '收起导航'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button>
          </div>
          <div className={styles.workspace}><ApartmentOutlined /><span>物流服务</span></div>
          <nav className={styles.navigation} aria-label="全局导航">
            {NAV_GROUPS.flatMap((group) => group.items).map((item) => {
              const isActive = active === item.key;
              return <button key={item.key} type="button" className={`${styles.navItem}${isActive ? ` ${styles.navItemActive}` : ''}`} aria-current={isActive ? 'page' : undefined} aria-label={item.label} onClick={() => navigate(item.key)}>
                {item.icon}<span className={styles.navLabel}>{item.label}</span>
                {item.key === '/approvals' && pending > 0 ? <span className={styles.navCount}>{pending}</span> : isActive ? <i className={styles.activeMark} aria-hidden="true" /> : null}
              </button>;
            })}
          </nav>
        </div>
        <div className={styles.sideFoot}>
          <Tooltip title={health.isSuccess ? '工作流服务连接正常' : health.isError ? '工作流服务不可用' : '正在检查服务状态'}>
            <span className={styles.healthPill}><span className={healthDotClass} /><span>{health.isSuccess ? '服务连接正常' : health.isError ? '服务连接异常' : '正在连接服务'}</span></span>
          </Tooltip>
          <div className={styles.session}>
            <span className={styles.avatar}><UserOutlined /></span>
            <span className={styles.sessionName}>工作台会话</span>
            <Button type="text" size="small" className={styles.themeBtn} icon={mode === 'dark' ? <MoonOutlined /> : <SunOutlined />} onClick={toggle} title="切换明暗主题" aria-label="切换明暗主题" />
          </div>
        </div>
      </aside>

      <Drawer title="工作台使用说明" open={helpOpen} onClose={() => setHelpOpen(false)} width="min(480px, 100vw)">
        <div className={styles.help}>
          <section><h3>查看执行批次</h3><p>按状态、批次 ID 和时间筛选记录。展开批次行查看最近动作，进入详情核对执行案卷、问题任务和恢复记录。</p></section>
          <section><h3>排查问题与证据</h3><p>进入任务工作台查看原始异常、修复差异、正式验证结论及各步骤的输入、输出和归档产物。</p></section>
          <section><h3>核对交付审批</h3><p>本地候选准备完成后，由人工核对提交与验证证据。批准后系统正式交付；驳回后按指定阶段回修。系统不会自动合并。</p></section>
          <section><h3>快捷检索</h3><p>按 Ctrl+K 搜索批次 ID，或快速进入执行批次与交付审批。</p></section>
        </div>
      </Drawer>
      <div className={styles.workarea}>
        <header className={styles.topbar}>
          <nav aria-label="工作空间路径" className={styles.breadcrumb}><span>工作空间</span><span>/</span><Link to={active}><i aria-hidden="true" />{active === '/approvals' ? '交付审批' : '执行批次'}</Link>{location.pathname.includes('/tasks/') && <><span>/</span><span>任务工作台</span></>}</nav>
          <div className={styles.topActions}>
            <WorkspaceSearch />
            <span className={styles.actionDivider} />
            <Button className={styles.docsButton} aria-label="文档" type="text" icon={<QuestionCircleOutlined />} onClick={() => setHelpOpen(true)}><span>文档</span></Button>
            <Popover trigger="click" placement="bottomRight" title="待处理消息" content={<div className={styles.notifications}><p>{pending > 0 ? `${pending} 个本地候选等待交付审批。` : '当前没有待审批交付。'}</p><Button type="link" size="small" onClick={() => navigate('/approvals')}>打开交付审批 <ArrowRightOutlined /></Button></div>}>
              <button className={styles.notificationButton} aria-label="待处理消息"><BellOutlined />{pending > 0 && <i aria-hidden="true" />}</button>
            </Popover>
            <Button type="text" className={styles.topRefresh} icon={<ReloadOutlined />} aria-label="刷新工作空间" title="同步状态" onClick={() => void queryClient.invalidateQueries()} />
          </div>
        </header>
        <main id="main-content" className={styles.main}><Outlet /></main>
      </div>


    </div>
  );
}
