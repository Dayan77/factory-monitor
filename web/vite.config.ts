import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Backend for the dev proxy (override to run against another API, e.g. API_PROXY_TARGET=http://localhost:8101).
const API_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true },
    },
  },
});
