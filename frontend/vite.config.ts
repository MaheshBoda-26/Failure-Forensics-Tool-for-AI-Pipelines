import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/traces": "http://127.0.0.1:8000",
      "/diagnoses": "http://127.0.0.1:8000",
      "/analytics": "http://127.0.0.1:8000",
      "/eval": "http://127.0.0.1:8000",
      "/pipeline": "http://127.0.0.1:8000",
    },
  },
});
