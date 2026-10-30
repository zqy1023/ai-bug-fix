import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'WORKBENCH_');
  return {
    plugins: [react()],
    build: {
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: {
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
