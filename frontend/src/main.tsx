import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import '@ant-design/v5-patch-for-react-19';
import 'antd/dist/reset.css';
import './styles/fonts.css';
import './styles.css';
import { AppRouter } from './app/router';
import { ThemeProvider, useThemeMode, getAntdTheme } from './app/ThemeProvider';
import { ApiError } from './lib/api';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 8000,
      refetchOnWindowFocus: true,
      // 404/401 是确定性结果，重试只会推迟错误态。
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && [401, 404].includes(error.status)),
    },
    mutations: { retry: 0 },
  },
});

/** AntD 主题与 --wb-* 变量共用同一个模式来源。 */
function ThemedApp() {
  const { mode } = useThemeMode();
  return (
    <ConfigProvider locale={zhCN} theme={getAntdTheme(mode)}>
      <QueryClientProvider client={queryClient}>
        <AntApp><AppRouter /></AntApp>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  </React.StrictMode>,
);
