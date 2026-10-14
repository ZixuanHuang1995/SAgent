import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開發時前端跑在 5173，/api 轉給後端 8765。打包後由後端直接提供前端檔案。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765' },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 4000,
  },
})
