import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 构建产物直接落进 Python 包目录（api/src/erp_tools/apps/server/static），
// 由 Starlette 在 /ui 上以 SPA 方式提供，部署时不需要 Node。
export default defineConfig({
  base: '/ui/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../api/src/erp_tools/apps/server/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时把后端接口代理到本机的 erp serve
      '/v1': 'http://127.0.0.1:8765',
      '/healthz': 'http://127.0.0.1:8765',
    },
  },
})
