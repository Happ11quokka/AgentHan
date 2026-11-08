import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 개발 중 /api 로 시작하는 요청(HTTP·SSE·WebSocket)을 FastAPI(8000)로 넘긴다.
    // HMR 소켓은 따로 구분되므로 이 규칙과 겹치지 않는다.
    proxy: {
      '/api': { target: 'http://localhost:8000', ws: true },
    },
  },
})
