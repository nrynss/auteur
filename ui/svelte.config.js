import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  // Svelte 5 compiles TypeScript natively; vitePreprocess keeps the Vite dev
  // pipeline and svelte-check on the same preprocessing view.
  preprocess: vitePreprocess(),
};
