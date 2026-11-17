import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发服务器：/api 代理到 FastAPI（compress: false 关闭缓冲，保证 SSE 实时）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        compress: false,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})