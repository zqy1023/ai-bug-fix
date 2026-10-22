import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const envDir = fileURLToPath(new URL('.', import.meta.url));
  const env = loadEnv(mode, envDir, 'WORKBENCH_');
  return {
    envDir,
    plugins: [react()],
    build: {
      // Ant Design v5 的运行时 chunk 约 1MB；页面代码已通过路由和 manualChunks 拆分。
      chunkSizeWarningLimit: 1100,
      rollupOptions: {
        output: {
          manualChunks: {
            antd: ['antd', '@ant-design/icons'],
            query: ['@tanstack/react-query'],
          },
        },
      },
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: env.WORKBENCH_API_ORIGIN?.trim() || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  };
});
