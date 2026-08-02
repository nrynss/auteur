import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

// Unit tests for the 7.7 component layer.
// - SSR contracts (Chip a11y, Evidence, format/theme logic): node env,
//   Svelte 5 server render().
// - DOM interaction (ConfirmDialog cancel rules): jsdom env, client mount().
// The browser condition makes `svelte` resolve to the client build so mount()
// works; "svelte/server" is a separate entry and is unaffected.
export default defineConfig({
  plugins: [svelte()],
  resolve: {
    conditions: ["browser"],
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
