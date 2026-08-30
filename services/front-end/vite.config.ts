import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies (strips the /api prefix), mirroring what nginx does in
// the Docker image:
//   /api/ws/*  -> ai-agents  (WebSocket answer stream)
//   /api/*     -> bot-agent  (chat submit + history)
// Override the targets with VITE_WS_PROXY_TARGET / VITE_PROXY_TARGET.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_PROXY_TARGET || 'http://localhost:8001';
  const wsTarget = env.VITE_WS_PROXY_TARGET || 'http://localhost:8000';

  return {
    plugins: [react()],
    server: {
      port: 3000,
      host: true,
      proxy: {
        '/api/ws': {
          target: wsTarget,
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
        '/api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
