import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // Ant Design v5 的运行时 chunk 约 1MB；页面代码已通过路由和 manualChunks 拆分。
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks: {
          antd: ['antd', '@ant-design/icons'],
          charts: ['echarts', 'echarts-for-react'],
          flow: ['@xyflow/react'],
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
        target: process.env.WORKBENCH_API_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
