/**
 * Fail-closed money guard for the demo harness.
 *
 * The paid SFX tier bills ~$0.10–0.30 per stem and director turns bill a
 * model call. Capture is navigation / hover / select / drag / playback —
 * free. This module never stubs GET traffic (filming mocks is forbidden);
 * it only aborts accidental billable mutations and records violations so
 * the test fails before a second take costs more.
 */
import type { Page, Request } from "@playwright/test";

/** POST/PUT paths that spend money or start billable work. */
const BILLABLE_PATH =
  /\/api\/v1\/projects\/[^/]+\/(director|mixes|exports)(?:\?|$)|\/events\/[^/]+\/regenerate(?:\?|$)/;

/** Button / dialog labels we must never activate. */
const FORBIDDEN_CLICK =
  /\b(mix|export|regenerate|confirm cost|confirm &|send)\b/i;

export interface Safety {
  violations: string[];
  assertClean: () => void;
}

function pathOf(req: Request): string {
  try {
    return new URL(req.url()).pathname;
  } catch {
    return req.url();
  }
}

export function isBillableRequest(req: Request): boolean {
  const method = req.method().toUpperCase();
  if (method !== "POST" && method !== "PUT" && method !== "PATCH") {
    return false;
  }
  // putTimeline (PUT …/timeline) is a free storage write — not billable.
  // We still avoid clicking Commit in the clips; this only filters money paths.
  const p = pathOf(req);
  if (method === "PUT" && /\/timeline$/.test(p)) return false;
  return BILLABLE_PATH.test(p);
}

/**
 * Install on every capture page. Continues all non-billable traffic
 * unchanged (real backend). Aborts billable mutations.
 */
export async function installSafety(page: Page): Promise<Safety> {
  const violations: string[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    if (isBillableRequest(req)) {
      const msg = `BILLABLE blocked: ${req.method()} ${pathOf(req)}`;
      violations.push(msg);
      console.error(`[demo-safety] ${msg}`);
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  // Defense: if a clip accidentally targets a spend control, refuse the click.
  await page.exposeFunction("__demoSafetyNote", (msg: string) => {
    violations.push(msg);
    console.error(`[demo-safety] ${msg}`);
  });

  await page.addInitScript((patternSource: string) => {
    const re = new RegExp(patternSource, "i");
    const labelOf = (el: EventTarget | null): string => {
      if (!(el instanceof Element)) return "";
      const btn = el.closest("button, [role='button'], a, input[type='submit']");
      if (!btn) return "";
      return (
        (btn.textContent || "") +
        " " +
        (btn.getAttribute("aria-label") || "") +
        " " +
        (btn.getAttribute("title") || "")
      );
    };
    document.addEventListener(
      "click",
      (e) => {
        const label = labelOf(e.target).trim();
        if (!label) return;
        // Allow "Foley mix" (A/B source toggle) — not the spend Mix action.
        if (/foley\s*mix/i.test(label) && !/\b(run|start|confirm)\b/i.test(label)) {
          return;
        }
        if (re.test(label)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          // @ts-expect-error injected
          window.__demoSafetyNote?.(`Forbidden click blocked: ${label.slice(0, 80)}`);
        }
      },
      true,
    );
  }, FORBIDDEN_CLICK.source);

  return {
    violations,
    assertClean() {
      if (violations.length) {
        throw new Error(
          `Demo safety violations (no billable action allowed):\n` +
            violations.map((v) => `  - ${v}`).join("\n"),
        );
      }
    },
  };
}
