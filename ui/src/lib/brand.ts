/** Brand surface (handoff.md task 19): the product name, the page-title
 * template and the favicon are build-time configuration, so a deployment can
 * rebrand without patching.
 *
 * Set at build time (Vite env — defaults are today's values, so a default
 * build renders byte-identical in appearance to before this existed):
 *
 *   VITE_BRAND_NAME             the product name               ("Auteur")
 *   VITE_BRAND_TITLE_TEMPLATE   "{name} — {page}"              (default)
 *   VITE_BRAND_FAVICON          any href — data URI or public  (default:
 *                               path under /public              inline SVG mark)
 *
 * The favicon is consumed by `vite.config.ts`, because index.html is
 * transformed at build time and cannot read `import.meta.env` — everything
 * else reads the same env vars here. Colours need nothing: the design tokens
 * in `ui/src/lib/theme.css` are consumed by components, never raw values, so
 * an accent override is a token file, not a patch (unit 7.7).
 *
 * Internals are deliberately not branded: the CLI (`foleyflow`), the package,
 * `FOLEYFLOW_*` env vars, the storage key space (`projects/`, `ledger/`,
 * `cache/`) and the `foleyflow-theme` localStorage key are never rendered.
 * Externalise only what a user sees.
 */

export const BRAND_NAME: string =
  import.meta.env.VITE_BRAND_NAME || "Auteur";

export const BRAND_TITLE_TEMPLATE: string =
  import.meta.env.VITE_BRAND_TITLE_TEMPLATE || "{name} — {page}";

/** "Control room" → "Auteur — Control room". Pure so the template
 * mechanics are unit-testable; a rebranded template moves the name and page
 * anywhere ("{name} · {page}"). replaceAll, not replace: a template that
 * repeats a placeholder ("{name} — {page} — {name}") must render every
 * occurrence, and a template without placeholders is a legal — if odd —
 * config used verbatim. */
export function formatPageTitle(
  template: string,
  name: string,
  page: string,
): string {
  return template.replaceAll("{name}", name).replaceAll("{page}", page);
}

export function brandPageTitle(page: string): string {
  return formatPageTitle(BRAND_TITLE_TEMPLATE, BRAND_NAME, page);
}
