import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Build target is the Python package's static dir, which api/dashboard.py
// serves with an SPA catch-all. `emptyOutDir` must be explicit because the
// output directory lives outside the Vite root (ui/).
// The dev server proxies /api and /docs to a locally running API so the SPA
// can be developed at :5173 against :8000 (production serves both from one
// origin, so the client uses same-origin relative paths either way).
export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/docs": "http://localhost:8000",
    },
  },
  build: {
    outDir: "../src/foleyflow/api/static",
    emptyOutDir: true,
  },
});
