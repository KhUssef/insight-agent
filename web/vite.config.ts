import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build output is the FastAPI app's static directory, so the Python
// server ships the frontend without needing Node at runtime. The dev server
// proxies every API route to a locally running uvicorn instance.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/insight_agent/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/ask": "http://127.0.0.1:8000",
      "/charts": "http://127.0.0.1:8000",
      "/meta": "http://127.0.0.1:8000",
      "/dataset": "http://127.0.0.1:8000",
      "/stats": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
