import { useState, type FormEvent } from 'react';
import { Link, NavLink, Outlet, useLocation, useMatch, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Icon } from '../components/ui';
import { shortId } from '../lib/format';
import { useRunEvents } from '../lib/hooks';

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'on' : undefined;
}

function Crumbs() {
  const { pathname } = useLocation();
  const parts = pathname.split('/').filter(Boolean);
  const crumbs: Array<{ label: string; to?: string }> = [{ label: '工作空间', to: '/' }];
  if (parts[0] === 'runs') {
    crumbs.push({ label: '批次', to: '/runs' });
    if (parts[1] && parts[1] !== 'new') {
      crumbs.push({ label: shortId(parts[1], 10), to: `/runs/${parts[1]}` });
      if (parts[2] === 'tasks' && parts[3]) {
        crumbs.push({ label: shortId(parts[3].split('__')[0], 18), to: pathname });
      } else if (parts[2] === 'records') {
        crumbs.push({ label: '记录' });
      } else if (parts[2] === 'artifacts') {
        crumbs.push({ label: '产物' });
      }
    } else if (parts[1] === 'new') {
      crumbs.push({ label: '新建' });
    }
  } else if (parts[0] === 'settings') {
    crumbs.push({ label: '设置' });
  } else {
    crumbs.push({ label: '概览' });
  }
  return (
    <div className="crumbs">
      {crumbs.map((crumb, index) => (
        <span className="row" key={`${crumb.label}-${index}`} style={{ gap: 8 }}>
          {index > 0 ? <span className="sep">/</span> : null}
          {crumb.to && index < crumbs.length - 1 ? <Link to={crumb.to}>{crumb.label}</Link> : <b>{crumb.label}</b>}
        </span>
      ))}
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const match = useMatch('/runs/:runId/*');
  const live = useRunEvents(match?.params.runId);
  const [keyword, setKeyword] = useState('');
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: 30_000 });
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15_000 });
  const activeCount = dashboard.data?.active_runs ?? 0;

  const onSearch = (event: FormEvent) => {
    event.preventDefault();
    const value = keyword.trim();
    navigate(value ? `/runs?keyword=${encodeURIComponent(value)}` : '/runs');
  };

  return (
    <div className="app">
      <aside className="rail">
        <div className="rail-brand">
          <div className="rail-logo">修</div>
          <div>
            <div className="name">修复工作台</div>
            <span className="sub">REPAIR ATLAS</span>
          </div>
        </div>

        <div className="rail-ws">
          <span>默认工作空间</span>
          <span className="caret">▾</span>
        </div>

        <nav className="rail-nav">
          <NavLink to="/" end className={navClass}>
            <Icon name="grid" />
            概览
          </NavLink>
          <NavLink to="/runs" className={navClass}>
            <Icon name="layers" />
            批次
            {activeCount > 0 ? <span className="count">{activeCount}</span> : null}
          </NavLink>
          <NavLink to="/settings" className={navClass}>
            <Icon name="settings" />
            设置
          </NavLink>
        </nav>

        <div className="rail-foot">
          <div className="health">
            <i style={{ background: health.data?.status === 'ok' ? '#22c55e' : '#f59e0b' }} />
            {health.isError ? '服务不可达' : health.data?.status === 'ok' ? '服务正常 · 全局串行' : '检查中'}
          </div>
          <div className="user">
            <span className="avatar">K</span>
            <div>
              <div style={{ color: '#cbd5e1', fontSize: 12 }}>kirby</div>
              <div style={{ color: '#64748b', fontSize: 10 }}>默认操作人</div>
            </div>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <Crumbs />
          <div className="top-right">
            {match ? <span className={`live-sync ${live.error ? 'offline' : ''}`} role="status">
              <i />{live.error ? '同步失败' : live.terminal ? '已同步最终结果' : live.streamState === 'open' ? '实时更新' : '自动同步中'}
            </span> : null}
            <form className="searchbox" onSubmit={onSearch}>
              <Icon name="search" />
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索批次、任务、承运商"
                aria-label="搜索批次、任务、承运商"
                style={{ border: 0, outline: 0, background: 'transparent', flex: 1, minWidth: 0, color: 'inherit' }}
              />
            </form>
            <button className="btn ghost" aria-label="通知" style={{ padding: 4 }}>
              <Icon name="bell" size={18} />
            </button>
            <span className="avatar" style={{ width: 28, height: 28, borderRadius: '50%', background: '#f1f5f9', display: 'grid', placeItems: 'center', fontSize: 11 }}>
              K
            </span>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
