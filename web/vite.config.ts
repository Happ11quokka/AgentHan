/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 중에는 /assistant(사장님 비서)와 /api(전화 주문, HTTP·SSE·WebSocket) 요청을 FastAPI로 넘긴다.
// 전화 서버(`python -m agenthan.voice.server`, 기본 :8000)는 /assistant도 같이 낸다.
// 다른 포트의 백엔드를 쓰려면 AGENTHAN_API=http://127.0.0.1:8001 npm run dev
const backend = process.env.AGENTHAN_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/assistant": backend,
      "/api": { target: backend, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
