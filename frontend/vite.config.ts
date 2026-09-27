import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev: `npm run dev` proxies API calls to the Flask app on :8080.
// Prod: `npm run build` -> dist/, served by Flask at "/" (assets under /assets/).
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/classic": "http://localhost:8080",
      "/decision": "http://localhost:8080",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts", "echarts-for-react"],
          vendor: ["react", "react-dom", "framer-motion", "@tanstack/react-query"],
        },
      },
    },
  },
});
