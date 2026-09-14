import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 只读 API（runs / eval / SSE 事件流）由 FastAPI 提供
      "/api": { target: "http://127.0.0.1:8010", ws: false },
    },
  },
});
