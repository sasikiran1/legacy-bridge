import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/internal": backend,
      "/api": backend,
      "/admin": backend,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
