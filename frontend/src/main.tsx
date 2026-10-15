import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'antd/dist/reset.css';
import '@xyflow/react/dist/style.css';
import './styles.css';
import { AppRouter } from './app/router';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
    mutations: { retry: 0 },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#3659e3',
          colorInfo: '#3659e3',
          borderRadius: 8,
          fontFamily: '"Inter", "PingFang SC", "Microsoft YaHei", sans-serif',
          colorBgLayout: '#f5f7fb',
          controlHeight: 36,
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <AppRouter />
        </AntApp>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);