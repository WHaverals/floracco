import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  // `npm run preview` serves the production build (where pilot-hidden tools
  // are actually hidden) against the same local API.
  preview: {
    port: 4173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
