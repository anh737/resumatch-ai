import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxy (strips the /api prefix), mirroring what nginx does in the
// Docker image:  /api/*  ->  bot-agent  (admin uploads, status, Langfuse proxy).
// Override the target with VITE_PROXY_TARGET.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_PROXY_TARGET || 'http://localhost:8001';

  return {
    plugins: [react()],
    server: {
      port: 3001,
      host: true,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
