import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../layout/AppLayout';
import { Spin } from 'antd';

const DashboardPage = lazy(() => import('../pages/DashboardPage').then(({ DashboardPage }) => ({ default: DashboardPage })));
const RunsPage = lazy(() => import('../pages/RunsPage').then(({ RunsPage }) => ({ default: RunsPage })));
const RunDetailPage = lazy(() => import('../pages/RunDetailPage').then(({ RunDetailPage }) => ({ default: RunDetailPage })));
const TaskDetailPage = lazy(() => import('../pages/TaskDetailPage').then(({ TaskDetailPage }) => ({ default: TaskDetailPage })));
const TaskAuditPage = lazy(() => import('../pages/TaskAuditPage').then(({ TaskAuditPage }) => ({ default: TaskAuditPage })));
const ApprovalsPage = lazy(() => import('../pages/ApprovalsPage').then(({ ApprovalsPage }) => ({ default: ApprovalsPage })));
const NotFoundPage = lazy(() => import('../pages/NotFoundPage').then(({ NotFoundPage }) => ({ default: NotFoundPage })));

function RouteFallback() {
  return <div className="page-loading"><Spin /><span>正在加载页面…</span></div>;
}

export function AppRouter() {
  return <BrowserRouter><Suspense fallback={<RouteFallback />}><Routes><Route element={<AppLayout />}>
    <Route index element={<Navigate to="/dashboard" replace />} />
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/runs" element={<RunsPage />} />
    <Route path="/audit" element={<TaskAuditPage />} />
    <Route path="/runs/:runId/tasks/:taskId" element={<TaskDetailPage />} />
    <Route path="/runs/:runId" element={<RunDetailPage />} />
    <Route path="/approvals" element={<ApprovalsPage />} />
    <Route path="*" element={<NotFoundPage />} />
  </Route></Routes></Suspense></BrowserRouter>;
}
