/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 중에는 /assistant 요청을 FastAPI(uv run ... uvicorn, 기본 :8000)로 넘긴다.
// 다른 포트의 백엔드를 쓰려면 AGENTHAN_API=http://127.0.0.1:8001 npm run dev
const backend = process.env.AGENTHAN_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/assistant": backend },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
