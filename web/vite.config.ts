/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 중에는 /assistant 요청을 FastAPI(uv run ... uvicorn, :8000)로 넘긴다.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/assistant": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
