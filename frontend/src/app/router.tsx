import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../layout/AppLayout';
import { DashboardPage } from '../pages/DashboardPage';
import { RunsPage } from '../pages/RunsPage';
import { RunDetailPage } from '../pages/RunDetailPage';
import { TaskDetailPage } from '../pages/TaskDetailPage';
import { ApprovalsPage } from '../pages/ApprovalsPage';
import { KnowledgePage } from '../pages/KnowledgePage';
import { NotFoundPage } from '../pages/NotFoundPage';
export function AppRouter() {
  return <BrowserRouter><Routes><Route element={<AppLayout />}>
    <Route index element={<Navigate to="/dashboard" replace />} />
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/runs" element={<RunsPage />} />
    <Route path="/runs/:runId/tasks/:taskId" element={<TaskDetailPage />} />
    <Route path="/runs/:runId" element={<RunDetailPage />} />
    <Route path="/approvals" element={<ApprovalsPage />} />
    <Route path="/knowledge" element={<KnowledgePage />} />
    <Route path="*" element={<NotFoundPage />} />
  </Route></Routes></BrowserRouter>;
}