import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import { AppShell } from '../layout/AppShell';

const RunsPage = lazy(() => import('../features/runs/RunsPage').then(({ RunsPage }) => ({ default: RunsPage })));
const RunWorkspace = lazy(() => import('../features/run/RunWorkspace').then(({ RunWorkspace }) => ({ default: RunWorkspace })));
const TaskWorkspace = lazy(() => import('../features/task/TaskWorkspace').then(({ TaskWorkspace }) => ({ default: TaskWorkspace })));
const ApprovalsPage = lazy(() => import('../features/approvals/ApprovalsPage').then(({ ApprovalsPage }) => ({ default: ApprovalsPage })));
const NotFoundPage = lazy(() => import('../pages/NotFoundPage').then(({ NotFoundPage }) => ({ default: NotFoundPage })));

function RouteFallback() {
  return <div className="route-fallback"><Spin /><span>正在加载视图…</span></div>;
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/runs" replace />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:runId" element={<RunWorkspace />} />
            <Route path="/runs/:runId/tasks/:taskId" element={<TaskWorkspace />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
