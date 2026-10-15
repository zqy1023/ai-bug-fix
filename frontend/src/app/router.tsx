import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../layout/AppLayout';
import { DashboardPage } from '../pages/DashboardPage';
import { RunsPage } from '../pages/RunsPage';
import { RunDetailPage } from '../pages/RunDetailPage';
import { ApprovalsPage } from '../pages/ApprovalsPage';
import { NotFoundPage } from '../pages/NotFoundPage';
export function AppRouter() {
  return <BrowserRouter><Routes><Route element={<AppLayout />}>
    <Route index element={<Navigate to="/dashboard" replace />} />
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/runs" element={<RunsPage />} />
    <Route path="/runs/:runId" element={<RunDetailPage />} />
    <Route path="/approvals" element={<ApprovalsPage />} />
    <Route path="*" element={<NotFoundPage />} />
  </Route></Routes></BrowserRouter>;
}