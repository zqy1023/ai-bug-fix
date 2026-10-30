import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './styles/fonts.css';
import './styles/app.css';
import { AppRouter } from './router';
import { ToastProvider } from './components/ui';
import { ApiError } from './lib/api';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 8_000,
      refetchOnWindowFocus: true,
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && [401, 404].includes(error.status)),
    },
    mutations: { retry: 0 },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AppRouter />
      </ToastProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
