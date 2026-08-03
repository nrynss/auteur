import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig, loadEnv } from "vite";

// The default favicon: an inline SVG waveform mark, served as a data URI —
// no asset file to lose or serve (kept here so the whole brand surface lives
// in config; a rebrand sets VITE_BRAND_FAVICON to any href — a data URI or a
// path under /public, which Vite copies into the build verbatim).
const DEFAULT_FAVICON_HREF =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='20' fill='%230E1113'/%3E%3Cpath d='M30 55 L45 25 L60 70 L75 40' stroke='%234FB3A6' stroke-width='10' fill='none' stroke-linecap='round'/%3E%3C/svg%3E";

// Build target is the Python package's static dir, which api/dashboard.py
// serves with an SPA catch-all. `emptyOutDir` must be explicit because the
// output directory lives outside the Vite root (ui/).
// The dev server proxies /api and /docs to a locally running API so the SPA
// can be developed at :5173 against :8000 (production serves both from one
// origin, so the client uses same-origin relative paths either way).
export default defineConfig(({ mode }) => {
  // "" prefix loads ALL vars into this config; only VITE_* flow to the client
  // bundle via import.meta.env (see ui/src/lib/brand.ts). Defaults are
  // today's values — a default build is byte-identical in appearance.
  const env = loadEnv(mode, process.cwd(), "");
  const brandName = env.VITE_BRAND_NAME?.trim() || "FoleyFlow";
  const faviconHref = env.VITE_BRAND_FAVICON?.trim() || DEFAULT_FAVICON_HREF;
  // Same title template the SPA uses (ui/src/lib/brand.ts), so the static
  // pre-hydration <title> and the runtime titles can never drift apart under
  // a rebrand.
  const titleTemplate = env.VITE_BRAND_TITLE_TEMPLATE || "{name} — {page}";
  const staticTitle = titleTemplate
    .replace("{name}", brandName)
    .replace("{page}", "Control room");

  return {
    plugins: [
      svelte(),
      {
        // The static <title> and the favicon live in the HTML shell, which
        // the client cannot rebrand at runtime — inject them at build time.
        // The <title> matches the SPA's own default (brand.ts page titles),
        // so pre-hydration and post-hydration agree.
        name: "foleyflow-brand",
        transformIndexHtml() {
          return [
            {
              tag: "title",
              children: staticTitle,
              injectTo: "head",
            },
            {
              tag: "link",
              attrs: { rel: "icon", href: faviconHref },
              injectTo: "head",
            },
          ];
        },
      },
    ],
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
  };
});
