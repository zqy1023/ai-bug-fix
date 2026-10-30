import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AppShell } from './layout/AppShell';
import { OverviewPage } from './pages/OverviewPage';
import { RunsPage } from './pages/RunsPage';
import { RunTasksPage } from './pages/RunTasksPage';
import { RunRecordsPage } from './pages/RunRecordsPage';
import { RunArtifactsPage } from './pages/RunArtifactsPage';
import { TaskOverviewPage } from './pages/TaskOverviewPage';
import { TaskChangePage } from './pages/TaskChangePage';
import { TaskReviewPage } from './pages/TaskReviewPage';
import { TaskAuditPage } from './pages/TaskAuditPage';
import { TaskAgentPage } from './pages/TaskAgentPage';
import { TaskPromptPage } from './pages/TaskPromptPage';
import { TaskArtifactsPage } from './pages/TaskArtifactsPage';
import { SettingsPage } from './pages/SettingsPage';
import { NotFoundPage } from './pages/NotFoundPage';

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunTasksPage />} />
          <Route path="/runs/:runId/records" element={<RunRecordsPage />} />
          <Route path="/runs/:runId/artifacts" element={<RunArtifactsPage />} />
          <Route path="/runs/:runId/tasks/:taskId" element={<TaskOverviewPage />} />
          <Route path="/runs/:runId/tasks/:taskId/change" element={<TaskChangePage />} />
          <Route path="/runs/:runId/tasks/:taskId/review" element={<TaskReviewPage />} />
          <Route path="/runs/:runId/tasks/:taskId/audit/steps" element={<TaskAuditPage />} />
          <Route path="/runs/:runId/tasks/:taskId/audit/agent" element={<TaskAgentPage />} />
          <Route path="/runs/:runId/tasks/:taskId/audit/prompt" element={<TaskPromptPage />} />
          <Route path="/runs/:runId/tasks/:taskId/audit/artifacts" element={<TaskArtifactsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
