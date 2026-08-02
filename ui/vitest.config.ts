import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

// Unit tests for the 7.7 component layer. Node environment + Svelte 5 SSR
// render() — no jsdom; the tests assert semantics (a11y contract, structural
// honesty), not pixels.
export default defineConfig({
  plugins: [svelte()],
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
